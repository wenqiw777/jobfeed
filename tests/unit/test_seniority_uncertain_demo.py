"""Behavioral examples for the offline uncertain-case experiment."""
import pytest
from scripts.demo_seniority_uncertain import judge

@pytest.mark.parametrize(('title','jd','expected'), [
    ('Software Engineer','**Basic Qualifications:**\n* 1\\+ years of programming experience','keep'),
    ('Software Engineer','Requirements\nAt least five years of professional experience','block'),
    ('Software Engineer','Requirements\n5+ years of experience selling or supporting technical sales','block'),
    ('Software Engineer','Requirements\nBachelor degree plus 2 years experience OR Master degree plus 0 years experience','review'),
    ('Software Engineer','Requirements\n2 years of experience\nPreferred Qualifications\n5 years of experience','keep'),
    ('Software Engineer','Our company has 60 years of experience.\nMust be 18 years of age.\nA four year university degree.','not_extracted'),
    ('Senior Software Engineer','Requirements\n3+ years of professional experience','review'),
    ('Junior / Senior Software Engineer','Requirements\nBuild software','review'),
    ('Software Engineer','Requirements\nBuild software with Python','not_extracted'),
])
def test_conservative_decisions(title,jd,expected):
    assert judge(title,jd)['decision']==expected
