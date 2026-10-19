import json
import tempfile
import unittest
from pathlib import Path
import workflow_guard as g


class ReadingReuse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mapping = self.root / 'source.json'
        self.index = self.root / 'reads.sqlite'
        self.rows = [{'id':str(i), 'base':f'第{i}段原文。'} for i in range(1,8)]
        g.save(self.mapping,self.rows)
        self.counter = 0

    def read(self, reader='编辑', **kwargs):
        self.counter += 1
        receipt = self.root / f'{self.counter}.json'
        body = g.read_slice(self.mapping, kwargs.pop('start','1'), kwargs.pop('end','7'),
                            'base', kwargs.pop('limit',8000), receipt,
                            reading_index=self.index,reader=reader,source_key='本书',**kwargs)
        return body,receipt

    def test_repeat_returns_note_without_manuscript(self):
        first,p = self.read()
        g.record_reading(p,'第4段人物反应与对白重复，见既有问题E04；其余保留。')
        body,unused = self.read()
        self.assertEqual('REUSE',json.loads(body)['action'])
        self.assertNotIn('第1段原文',body)
        self.assertIn('E04',body)
        self.assertFalse(unused.exists())

    def test_pending_read_is_not_fake_completion(self):
        self.read()
        body,_ = self.read()
        self.assertIn('未登记结论',body)

    def test_independent_role_reads_source(self):
        self.read()
        body,_ = self.read('逻辑家')
        self.assertIn('第1段原文',body)

    def test_changed_paragraph_and_neighbors_only(self):
        _,p = self.read()
        g.record_reading(p,'首次连读七段完成，问题E04待作家处理。')
        self.rows[3]['base']='第4段改后正文。'
        g.save(self.mapping,self.rows)
        with self.assertRaisesRegex(ValueError,'PARTLY_ALREADY_READ'):
            self.read()
        body,p = self.read(changed_only=True)
        self.assertEqual(['3','4','5'],g.load(p)['ids'])
        self.assertEqual(['4'],g.load(p)['indexed_ids'])
        self.assertNotIn('第1段原文',body)
        g.record_reading(p,'E04已检查，新候选第4段仍待用户评价。')

    def test_subrange_reuses_prior_record(self):
        self.read()
        body,_ = self.read(start='2',end='4')
        self.assertEqual('REUSE',json.loads(body)['action'])

    def test_source_path_change_does_not_erase_memory(self):
        self.read()
        other=self.root/'renamed.json'
        self.mapping.rename(other); self.mapping=other
        body,_ = self.read()
        self.assertEqual('REUSE',json.loads(body)['action'])

    def test_reason_allows_targeted_recovery(self):
        self.read()
        body,p = self.read(start='3',end='5',reread_reason='上下文压缩后无法确定第4段动作主语，恢复前后相邻段。')
        self.assertIn('第4段原文',body)
        self.assertTrue(g.load(p)['reread_reason'])

    def test_user_rejection_invalidates_all_roles_and_old_receipt(self):
        _,p = self.read()
        self.read('逻辑家')
        g.invalidate_reading(self.index,'本书',['4'],'用户否定第4段人物描写；旧通过撤销。')
        with self.assertRaisesRegex(ValueError,'READ_RECEIPT_SUPERSEDED'):
            g.record_reading(p,'旧记录不能恢复通过。')
        for role in ['编辑','逻辑家']:
            body,_ = self.read(role,changed_only=True)
            self.assertIn('第4段原文',body)

    def test_failed_large_output_does_not_mark_read(self):
        with self.assertRaisesRegex(ValueError,'OUTPUT_LIMIT'):
            self.read(limit=5)
        body,_=self.read()
        self.assertIn('第1段原文',body)


if __name__=='__main__': unittest.main()
