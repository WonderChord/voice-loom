"""Simulated devices exercise audio lifecycle without opening a microphone."""
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend import audio_engine
from backend.vad import SpeechGate


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


class VoiceDetector:
    def __init__(self):
        self.calls = []

    def is_speech(self, data, rate):
        self.calls.append((len(data), rate, threading.get_ident()))
        return True


class VADTests(unittest.TestCase):
    def gate(self, rate=16000, **kwargs):
        return SpeechGate(rate, detector=VoiceDetector(), **kwargs)

    def test_silence_and_single_click_are_rejected(self):
        gate = self.gate()
        samples = np.zeros(16000, np.float32)
        samples[1000] = 1
        self.assertEqual(gate.feed(samples) + gate.flush(), [])

    def test_max_duration_splits_continuously_without_dropping_speech(self):
        rate = 16000
        gate = self.gate(rate, max_seconds=5)
        signal = np.full(rate * 12 + 109, .2, np.float32)
        result = []
        for start in range(0, len(signal), 997):
            result.extend(gate.feed(signal[start:start + 997]))
        result.extend(gate.flush())
        self.assertEqual(len(result), 3)
        self.assertTrue(all(len(segment) <= rate * 5 for segment in result))
        np.testing.assert_array_equal(np.concatenate(result)[:, 0], signal)

    def test_short_remainder_after_hard_split_is_preserved(self):
        signal = np.full(16000 * 5 + 111, .2, np.float32)
        gate = self.gate(max_seconds=5)
        result = gate.feed(signal) + gate.flush()
        self.assertEqual([len(v) for v in result], [80000, 111])

    def test_endpoint_retains_only_short_silence_tail(self):
        gate = self.gate(pause_ms=900)
        signal = np.concatenate((np.full(16000, .2, np.float32),
                                 np.zeros(16000, np.float32)))
        result = gate.feed(signal)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 16000 + 4800)
        self.assertEqual(gate.flush(), [])

    def test_detector_resampling_preserves_capture_rate_audio(self):
        gate = self.gate(44100)
        signal = np.full((8820, 2), .2, np.float32)
        result = gate.feed(signal) + gate.flush()
        self.assertEqual(result[0].shape, (8820, 1))
        self.assertTrue(all(call[:2] == (640, 16000) for call in gate.vad.calls))

    def test_reset_clears_pending_utterance(self):
        gate = self.gate()
        gate.feed(np.full(8000, .2, np.float32))
        gate.reset()
        self.assertEqual(gate.flush(), [])

    def test_nonfinite_samples_are_rejected(self):
        with self.assertRaises(ValueError):
            self.gate().feed(np.array([np.nan], np.float32))


class FakeInput:
    def __init__(self, driver, **kwargs):
        self.driver = driver
        self.kwargs = kwargs
        self.active = self.closed = False
        driver.inputs.append(self)

    def start(self):
        self.active = True

    def abort(self):
        self.active = False

    def close(self):
        self.closed = True
        self.active = False

    def emit(self, audio, status=None):
        self.kwargs['callback'](audio, len(audio), None, status)


class FakeOutput(FakeInput):
    def __init__(self, driver, **kwargs):
        self.driver = driver
        self.kwargs = kwargs
        self.active = self.closed = False
        self.data = []
        driver.outputs.append(self)

    def write(self, data):
        if self.driver.fail_write:
            raise RuntimeError("device disconnected")
        self.data.append(data.copy())
        self.driver.write_entered.set()
        if self.driver.block_write:
            self.driver.write_release.wait(2)
        return False

    def abort(self):
        super().abort()
        self.driver.write_release.set()

    def stop(self):
        self.active = False


class FakeDriver:
    def __init__(self):
        self.default = SimpleNamespace(device=(0, 1))
        self.inputs = []
        self.outputs = []
        self.fail_write = self.block_write = False
        self.write_entered = threading.Event()
        self.write_release = threading.Event()
        self.output_format = None
        self.devices = [
            dict(name="测试麦克风", hostapi=0, default_samplerate=44100,
                 max_input_channels=2, max_output_channels=0),
            dict(name="测试耳机", hostapi=0, default_samplerate=48000,
                 max_input_channels=0, max_output_channels=2),
        ]

    def query_devices(self, device=None, kind=None):
        if device is None:
            return self.devices
        return self.devices[device]

    def query_hostapis(self):
        return [{"name": "Windows WASAPI"}]

    def check_input_settings(self, **kwargs):
        if kwargs['samplerate'] != 44100:
            raise ValueError("unsupported rate")

    def check_output_settings(self, **kwargs):
        expected = self.output_format or (48000, 1)
        if (kwargs['samplerate'], kwargs['channels']) != expected:
            raise ValueError("unsupported format")

    def InputStream(self, **kwargs):
        return FakeInput(self, **kwargs)

    def OutputStream(self, **kwargs):
        return FakeOutput(self, **kwargs)


class AudioEngineTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.statuses = []
        self.segments = []
        self.detector = VoiceDetector()
        self.patches = [
            patch.object(audio_engine, 'sd', self.driver),
            patch.object(audio_engine, 'SpeechGate', lambda *args, **kwargs:
                         SpeechGate(*args, **kwargs, detector=self.detector)),
            patch.object(audio_engine.sf, 'read', return_value=(
                np.full((2400, 1), .2, np.float32), 24000)),
        ]
        for item in self.patches:
            item.start()
        self.engine = audio_engine.AudioEngine(
            lambda data, rate: self.segments.append((data, rate)), self.statuses.append)
        self.config = dict(input_device=0, pause_ms=300, min_db=-44,
                           max_seconds=5, suppress_during_playback=True)

    def tearDown(self):
        self.engine.stop()
        for item in reversed(self.patches):
            item.stop()

    def emit_voice(self, frames=10):
        for _ in range(frames):
            self.driver.inputs[-1].emit(np.full((882, 1), .2, np.float32))

    def test_enumeration_reports_device_ids_and_defaults_without_opening(self):
        listing = audio_engine.list_devices()
        self.assertEqual(listing['default_input'], 0)
        self.assertEqual(listing['default_output'], 1)
        self.assertEqual(listing['inputs'][0]['hostapi'], 'Windows WASAPI')
        self.assertEqual([d['id'] for d in listing['outputs']], [1])
        self.assertEqual(self.driver.inputs + self.driver.outputs, [])

    def test_start_is_explicit_and_dsp_runs_outside_capture_callback(self):
        self.assertEqual(self.driver.inputs, [])
        self.engine.start(self.config)
        callback_thread = threading.get_ident()
        self.emit_voice()
        self.engine.flush()
        self.assertTrue(until(lambda: bool(self.segments)))
        self.assertEqual(self.segments[0][1], 44100)
        self.assertEqual(len(self.segments[0][0]), 8820)
        self.assertTrue(all(call[2] != callback_thread for call in self.detector.calls))

    def test_capture_from_api_worker_keeps_com_until_device_closes(self):
        state = threading.local()
        lifecycle = []

        @contextmanager
        def apartment():
            state.depth = getattr(state, 'depth', 0) + 1
            try:
                yield
            finally:
                state.depth -= 1

        owner = self

        class ComInput(FakeInput):
            def __init__(self, driver, **kwargs):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                lifecycle.append(('open', threading.get_ident()))
                super().__init__(driver, **kwargs)

            def start(self):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                super().start()

            def close(self):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                lifecycle.append(('close', threading.get_ident()))
                super().close()

        errors = []

        def request():
            try:
                self.engine.start(self.config)
            except Exception as exc:
                errors.append(exc)

        with patch.object(audio_engine, '_windows_com', apartment), patch.object(
                self.driver, 'InputStream', side_effect=lambda **kwargs: ComInput(self.driver, **kwargs)):
            worker = threading.Thread(target=request)
            worker.start()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertNotEqual(lifecycle[0][1], worker.ident)
            capture = self.engine._capture
            self.driver.inputs[0].active = False
            capture.thread.join(1)
            self.assertFalse(capture.thread.is_alive())
            self.assertEqual([step for step, _ in lifecycle], ['open', 'close'])
            self.assertEqual(lifecycle[0][1], lifecycle[1][1])

    def test_async_output_initializes_com_before_device_start(self):
        state = threading.local()
        lifecycle = []

        @contextmanager
        def apartment():
            state.depth = getattr(state, 'depth', 0) + 1
            try:
                yield
            finally:
                state.depth -= 1

        owner = self

        class ComOutput(FakeOutput):
            def __init__(self, driver, **kwargs):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                lifecycle.append('open')
                super().__init__(driver, **kwargs)

            def start(self):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                lifecycle.append('start')
                super().start()

            def write(self, data):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                return super().write(data)

            def close(self):
                owner.assertGreater(getattr(state, 'depth', 0), 0)
                lifecycle.append('close')
                super().close()

        with patch.object(audio_engine, '_windows_com', apartment), patch.object(
                self.driver, 'OutputStream', side_effect=lambda **kwargs: ComOutput(self.driver, **kwargs)):
            self.engine.play('simulation.wav', device=1, blocking=False)
            self.assertTrue(until(lambda: not self.engine.is_playing))
            self.assertEqual(lifecycle, ['open', 'start', 'close'])
            self.assertFalse(any('error' in item for item in self.statuses))

    def test_stop_discards_buffered_audio_and_closes_device(self):
        self.engine.start(self.config)
        self.emit_voice()
        self.engine.stop()
        self.engine.flush()
        self.assertEqual(self.segments, [])
        self.assertTrue(self.driver.inputs[0].closed)
        self.assertFalse(self.driver.inputs[0].active)

    def test_normal_silence_produces_no_segments_or_repeated_messages(self):
        self.engine.start(self.config)
        before = len(self.statuses)
        for _ in range(50):
            self.driver.inputs[-1].emit(np.zeros((882, 1), np.float32))
        self.engine.flush()
        self.assertTrue(until(lambda: self.engine._capture.packets.empty()))
        self.assertEqual(self.segments, [])
        self.assertFalse(any(item.get('message') or item.get('error')
                             for item in self.statuses[before:]))

    def test_unplugged_input_is_visible_and_closed(self):
        self.engine.start(self.config)
        self.driver.inputs[0].active = False
        self.assertTrue(until(lambda: any('error' in item for item in self.statuses)))
        self.assertTrue(until(lambda: self.driver.inputs[0].closed))
        self.assertTrue(any(item.get('listening') is False for item in self.statuses))
        self.assertEqual(next(item['error_source'] for item in self.statuses if 'error' in item), 'input')

    def test_bad_input_device_is_reported_without_opening(self):
        with self.assertRaises(RuntimeError):
            self.engine.start({**self.config, 'input_device': 20})
        self.assertTrue(any('error' in item for item in self.statuses))
        self.assertEqual(self.driver.inputs, [])

    def test_play_resamples_to_supported_device_rate(self):
        self.engine.play('simulation.wav', device=1)
        stream = self.driver.outputs[0]
        self.assertEqual(stream.kwargs['samplerate'], 48000)
        self.assertEqual(np.concatenate(stream.data).shape, (4800, 1))
        self.assertTrue(stream.closed)
        self.assertFalse(self.engine.is_playing)

    def test_play_falls_back_to_supported_stereo_format(self):
        self.driver.output_format = (44100, 2)
        self.engine.play('simulation.wav', device=1)
        audio = np.concatenate(self.driver.outputs[0].data)
        self.assertEqual(audio.shape, (4410, 2))
        np.testing.assert_array_equal(audio[:, 0], audio[:, 1])

    def test_resampling_preserves_pitch_amplitude_and_stereo_channels(self):
        # Independent analytic waveforms catch incorrect ratios, axis changes,
        # or channel mixing; all writes go only to FakeOutput.data in memory.
        self.driver.output_format = (48000, 2)

        def waveform(rate):
            t = np.arange(rate) / rate
            return np.column_stack((
                .2 * np.sin(2 * np.pi * 700 * t) + .1 * np.sin(2 * np.pi * 3000 * t),
                .17 * np.cos(2 * np.pi * 997 * t) + .07 * np.sin(2 * np.pi * 5000 * t),
            ))

        expected = waveform(48000)
        for source_rate in (24000, 44100):
            with self.subTest(source_rate=source_rate), patch.object(
                    audio_engine.sf, 'read', return_value=(
                        waveform(source_rate).astype(np.float32), source_rate)):
                self.engine.play('analytic-fixture.wav', device=1)
            stream = self.driver.outputs[-1]
            actual = np.concatenate(stream.data)
            self.assertEqual(actual.shape, expected.shape)
            self.assertEqual(stream.kwargs['samplerate'], 48000)
            self.assertTrue(stream.closed)
            # Exclude 10 ms at either end, where the FIR sees zero padding.
            delta = actual[480:-480] - expected[480:-480]
            relative_rms = np.sqrt(np.mean(delta ** 2) / np.mean(expected[480:-480] ** 2))
            self.assertLess(relative_rms, .002)
            self.assertLess(float(np.max(np.abs(delta))), .001)

    def test_cancel_during_file_loading_prevents_late_playback(self):
        entered, release = threading.Event(), threading.Event()

        def read(*args, **kwargs):
            entered.set()
            release.wait(2)
            return np.ones((2400, 1), np.float32), 24000

        with patch.object(audio_engine.sf, 'read', side_effect=read):
            self.engine.play('simulation.wav', blocking=False)
            playback = self.engine._playback
            self.assertTrue(self.engine.is_playing)
            self.assertTrue(entered.wait(1))
            self.engine.stop_playback()
            release.set()
            playback.thread.join(1)
        self.assertEqual(self.driver.outputs, [])
        self.assertFalse(self.engine.is_playing)

    def test_stop_aborts_current_output(self):
        self.driver.block_write = True
        self.engine.play('simulation.wav', blocking=False)
        self.assertTrue(self.driver.write_entered.wait(1))
        self.assertTrue(self.engine.is_playing)
        self.engine.stop()
        self.assertTrue(self.driver.outputs[0].closed)
        self.assertFalse(self.engine.is_playing)

    def test_output_device_loss_reports_error_and_does_not_retry(self):
        self.driver.fail_write = True
        with self.assertRaises(RuntimeError):
            self.engine.play('simulation.wav')
        self.assertEqual(len(self.driver.outputs), 1)
        self.assertTrue(self.driver.outputs[0].closed)
        self.assertTrue(any('error' in item for item in self.statuses))
        self.assertFalse(self.engine.is_playing)
        self.assertEqual(next(item['error_source'] for item in self.statuses if 'error' in item), 'output')

    def test_playback_suppresses_capture_then_allows_new_speech(self):
        self.engine.start(self.config)
        self.driver.block_write = True
        self.engine.play('simulation.wav', blocking=False)
        self.assertTrue(self.driver.write_entered.wait(1))
        self.emit_voice()
        self.engine.flush()
        self.assertTrue(until(lambda: any('暂停收音' in item.get('message', '')
                                         for item in self.statuses)))
        self.engine.stop_playback()
        self.emit_voice()
        self.engine.flush()
        self.assertTrue(until(lambda: bool(self.segments)))
        self.assertEqual(len(self.segments), 1)
        self.assertEqual(len(self.segments[0][0]), 8820)

    def test_queue_overflow_is_visible_and_callback_does_not_block(self):
        entered, release = threading.Event(), threading.Event()

        class SlowGate:
            def feed(self, data):
                entered.set()
                release.wait(2)
                return []

            def reset(self):
                pass

            def flush(self):
                return []

        self.engine.CAPTURE_QUEUE_SIZE = 2
        with patch.object(audio_engine, 'SpeechGate', return_value=SlowGate()):
            self.engine.start(self.config)
        self.emit_voice(1)
        self.assertTrue(entered.wait(1))
        begin = time.monotonic()
        self.emit_voice(20)
        self.assertLess(time.monotonic() - begin, .2)
        release.set()
        self.assertTrue(until(lambda: any('输入队列已满' in item.get('message', '')
                                         for item in self.statuses)))

    def test_inflight_dsp_result_is_discarded_after_stop(self):
        entered, release = threading.Event(), threading.Event()

        class SlowGate:
            def feed(self, data):
                entered.set()
                release.wait(2)
                return [data]

            def reset(self):
                pass

        with patch.object(audio_engine, 'SpeechGate', return_value=SlowGate()):
            self.engine.start(self.config)
        capture = self.engine._capture
        self.emit_voice(1)
        self.assertTrue(entered.wait(1))
        self.engine.stop()
        release.set()
        capture.thread.join(1)
        self.assertEqual(self.segments, [])


if __name__ == '__main__':
    unittest.main()
