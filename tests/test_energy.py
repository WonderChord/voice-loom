"""Energy decisions use synthetic arrays and files; no devices are opened."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from backend.energy import check_audio_energy, check_file_energy


class EnergyTests(unittest.TestCase):
    def test_silence_and_low_noise_are_rejected(self):
        for samples in (np.zeros(16000), np.full(16000, 1e-4)):
            result = check_audio_energy(samples, 16000)
            self.assertFalse(result.accepted)
            self.assertEqual(result.active_ms, 0)
            self.assertIn("已跳过识别", result.reason)

    def test_isolated_impulse_does_not_count_as_speech(self):
        samples = np.zeros(16000)
        samples[500] = 1
        result = check_audio_energy(samples, 16000)
        self.assertFalse(result.accepted)
        self.assertEqual(result.active_ms, 20)

    def test_voice_between_long_silences_is_not_diluted(self):
        samples = np.concatenate((np.zeros(16000 * 20), np.full(1920, .01),
                                  np.zeros(16000 * 20)))
        result = check_audio_energy(samples, 16000)
        self.assertTrue(result.accepted)
        self.assertEqual(result.active_ms, 120)
        self.assertEqual(result.reason, "")

    def test_active_time_accumulates_across_pauses(self):
        samples = np.tile(np.concatenate((np.full(320, .1), np.zeros(1600))), 6)
        self.assertTrue(check_audio_energy(samples, 16000).accepted)

    def test_partial_frame_counts_its_actual_duration(self):
        result = check_audio_energy(np.full(1919, .1), 16000)
        self.assertFalse(result.accepted)
        self.assertAlmostEqual(result.active_ms, 119.938, places=3)
        self.assertTrue(check_audio_energy(np.full(1920, .1), 16000).accepted)

    def test_stereo_mix_matches_asr_input(self):
        samples = np.column_stack((np.full(3200, .1), np.full(3200, -.1)))
        self.assertFalse(check_audio_energy(samples, 16000).accepted)
        samples[:, 1] = samples[:, 0]
        self.assertTrue(check_audio_energy(samples, 16000).accepted)

    def test_invalid_samples_and_rate_are_rejected(self):
        for samples, rate in (([], 16000), ([np.nan], 16000), ([1], 0),
                              ([1], float('inf')), (np.zeros((1, 0)), 16000)):
            with self.assertRaises(ValueError):
                check_audio_energy(samples, rate)

    def test_file_uses_config_and_disabled_gate_does_not_read(self):
        self.assertIsNone(check_file_energy('does-not-exist.wav', {'skip_low_energy': False}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'quiet.wav'
            sf.write(path, np.full(3200, .001), 16000)
            self.assertFalse(check_file_energy(path, {}).accepted)
            self.assertTrue(check_file_energy(path, {'energy_threshold_db': -70}).accepted)


if __name__ == '__main__':
    unittest.main()
