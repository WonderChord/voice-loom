"""Drama3 director fields, scoped chunks and real wire encoding; no paid generation."""
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

import msgpack
import numpy as np
from pydantic import ValidationError
import soundfile as sf

from backend import performance as perf, providers as p
from backend.models import Config, Plan, Segment
from backend.pipeline import validate_listening_config, ListeningSetupIssue

FISH = {'id':'fish','name':'验收 Fish','kind':'tts','type':'fish_tts','model':'drama-3','voice':'fixture-voice','api_key':'fixture-only'}
LOCAL = {'id':'local','kind':'tts','type':'drama3_local','base_url':'https://example.test','model':'drama-3'}

def speech(text, **extra):
    return {'kind':'speech','text':text,'emotion':'neutral','intensity':0.5,'delivery':'normal','speed':1,**extra}

def plan(*segments):
    text=''.join(s.get('text','') for s in segments if s['kind']=='speech')
    return {'source_text':text,'text':text,'segments':list(segments),'warnings':[]}

def wav(rate=48000):
    output=io.BytesIO()
    sf.write(output, np.sin(np.arange(2400)*0.1)*0.05, rate, format='WAV')
    return output.getvalue()

class DramaDirectionTests(unittest.TestCase):
    def test_structured_direction_survives_schema_and_every_scope_is_paired(self):
        direction='落ち着いて、友達に秘密を伝える声で'
        for provider in (FISH,LOCAL):
            for delivery in perf.DRAMA_DELIVERIES:
                candidate=plan(speech('这句才会朗读。',direction=direction,delivery=delivery))
                checked=p.validate_plan(Plan.model_validate(candidate).model_dump())
                self.assertEqual(checked['segments'][0]['direction'],direction)
                compiled=perf.compile_speech(provider,checked['text'],'sad',0.8,delivery,direction)
                self.assertEqual(compiled,f'[{direction}] <{delivery}>这句才会朗读。</{delivery}>')
                self.assertEqual(candidate['text'],'这句才会朗读。')
        for bad in ('[开心]', '<whisper>偷偷说', '第一行\n第二行', '字'*241, None):
            with self.subTest(bad=bad), self.assertRaises((ValueError,p.ProviderError)):
                p.validate_plan(plan(speech('你好',direction=bad)))

    @patch.object(p,'_speech',return_value=(np.ones(100,dtype=np.float32)*0.05,44100))
    def test_long_dialogue_keeps_direction_closes_tags_and_preserves_transcript(self, synth):
        dialogue='这里是第一句，后面还有一些说明。这里是第二句，请把故事完整地讲清楚。'*4
        candidate=plan({'kind':'event','event':'chuckle'},speech(dialogue,delivery='whisper',direction='克制而温柔地告诉朋友'))
        before=copy.deepcopy(candidate)
        with tempfile.TemporaryDirectory() as directory:
            p.synthesize(FISH,candidate,None,Path(directory)/'out.wav',{})
        texts=[c.args[1] for c in synth.call_args_list]
        self.assertGreater(len(texts),2)
        self.assertIn('[chuckling]',texts[0])
        for text in texts:
            self.assertIn('[克制而温柔地告诉朋友]',text)
            self.assertEqual(text.count('<whisper>'),text.count('</whisper>'))
            self.assertLessEqual(len(re.sub(r'\[[^]]*\]|<[^>]*>','',text).strip()),42)
        spoken=''.join(re.sub(r'\[[^]]*\]|<[^>]*>','',text).strip() for text in texts)
        self.assertEqual(spoken,dialogue)
        self.assertEqual(candidate,before)

    def test_splitter_preserves_unicode_and_words_at_spaces(self):
        for text in ('こんなふうに、ゆっくり話すと伝わるよ。'*8, 'Natural phrases, with room to breathe. '*8, 'e\u0301'*50):
            chunks=perf.split_spoken_text(text,42)
            self.assertEqual(''.join(chunks),text)
            self.assertTrue(all(len(c)<=42 for c in chunks))
            self.assertFalse(any(c.startswith('\u0301') for c in chunks))

    @patch.object(p,'_speech',return_value=(np.ones(100,dtype=np.float32)*0.05,44100))
    def test_scope_stops_at_next_normal_segment_and_can_disable_chunking(self,synth):
        with tempfile.TemporaryDirectory() as directory:
            p.synthesize(FISH,plan(speech('轻声说。',delivery='soft',direction='贴近但清晰'),speech('恢复正常。')),
                         None,Path(directory)/'out.wav',{})
            self.assertEqual(synth.call_args_list[1].args[1],'恢复正常。')
            synth.reset_mock()
            p.synthesize({**FISH,'options':{'drama_max_chars':0}},plan(speech('较长的完整句子。'*20)),None,Path(directory)/'long.wav',{})
            self.assertEqual(synth.call_count,1)

    @patch.object(p,'_speech',return_value=(np.ones(100,dtype=np.float32)*0.05,24000))
    def test_fallback_never_speaks_unsupported_direction_or_scope(self,synth):
        provider={'type':'openai_tts','model':'tts-1'}
        with tempfile.TemporaryDirectory() as directory:
            result=p.synthesize(provider,plan(speech('欢迎回来。',delivery='soft',direction='轻松地微笑')),
                                None,Path(directory)/'out.wav',{})
        self.assertEqual(synth.call_args.args[1],'欢迎回来。')
        self.assertEqual(synth.call_args.kwargs['delivery'],'normal')
        self.assertTrue(any('表演指导' in w for w in result['warnings']))

    @patch.object(p.requests,'Session')
    def test_msgpack_transmits_original_48k_reference_and_exact_transcript(self,session):
        request=session.return_value.__enter__.return_value.request
        request.return_value=Mock(status_code=200,content=wav(44100))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'reference.wav'
            audio=wav()
            path.write_bytes(audio)
            transcript='ふふっ、そうだね〜。'
            p.synthesize({**FISH,'voice':''},plan(speech('こんばんは。',delivery='soft',direction='穏やかに')),
                         {'path':str(path),'transcript':transcript},Path(directory)/'out.wav',{})
        call=request.call_args
        self.assertNotIn('json',call.kwargs)
        self.assertEqual(call.kwargs['headers']['Content-Type'],'application/msgpack')
        self.assertEqual(call.kwargs['headers']['model'],'drama-3')
        payload=msgpack.unpackb(call.kwargs['data'],raw=False)
        self.assertEqual(payload['references'],[{'audio':audio,'text':transcript}])
        self.assertNotIn('reference_id',payload)
        self.assertEqual(payload['sample_rate'],44100)
        self.assertEqual(payload['text'],'[穏やかに] <soft>こんばんは。</soft>')

    @patch.object(p,'_reference_file')
    @patch.object(p,'_request',return_value=Mock(content=wav(44100)))
    def test_saved_voice_id_takes_precedence_without_uploading_reference(self,request,read):
        p._speech(FISH,'你好','neutral',0,{'path':'missing'},'selected-voice')
        read.assert_not_called()
        self.assertEqual(request.call_args.kwargs['json']['reference_id'],'selected-voice')

    def test_fish_listening_accepts_reference_without_voice_id_and_rejects_no_transcript(self):
        cfg=Config().model_dump()
        cfg['providers'].append({**FISH,'voice':''})
        cfg['active']['tts']='fish'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ref.wav';path.write_bytes(wav())
            validate_listening_config(cfg,{'path':str(path),'transcript':'准确台词'})
            with self.assertRaises(ListeningSetupIssue):
                validate_listening_config(cfg,{'path':str(path),'transcript':''})

    def test_invalid_chunk_limits_are_rejected_before_saving(self):
        for limit in (True, -1, 3, 42.5, '42',301):
            cfg=Config().model_dump()
            cfg['providers'].append({**FISH,'options':{'drama_max_chars':limit}})
            with self.subTest(limit=limit),self.assertRaises(ValidationError):
                Config.model_validate(cfg)

    @patch.object(p,'_request')
    def test_generator_and_auditor_keep_director_separate_from_spoken_text(self,request):
        candidate=plan(speech('请进。',direction='微笑着，像欢迎老朋友',delivery='soft'))
        def response(value):
            return Mock(json=lambda:{'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}]})
        request.side_effect=[response(candidate),response({'passed':True,'uncertain':False,'message':'通过','leaked_instructions':[]})]
        config={'rewrite_enabled':True,'active':{'tts':'fish'},'providers':[FISH]}
        result=p.rewrite({'type':'openai_llm','model':'fixture'},'请进。',[],config)
        self.assertTrue(result['audit']['passed'])
        self.assertEqual(result['text'],'请进。')
        self.assertEqual(result['segments'][0]['direction'],candidate['segments'][0]['direction'])
        generation=request.call_args_list[0].kwargs['json']['messages'][0]['content']
        audit=json.loads(request.call_args_list[1].kwargs['json']['messages'][1]['content'])
        self.assertIn('speech.direction',generation)
        self.assertIn('<soft>',generation)
        self.assertEqual(audit['candidate']['segments'][0]['delivery'],'soft')
        self.assertNotIn('fixture-only',generation)
