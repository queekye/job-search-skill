"""Identify explicit editorial instructions in fields used as resume text.

Never silently rewrite user content or strip factual qualifications. Corrections
use ordinary reviewed record edits; originals remain in revision history.
"""
import re


EDITORIAL = re.compile(
    r'论文题名不添加|不拼进论文题名|不把[^。\n]*误述为|'
    r'不作为当前[^。\n]*独立核验|原简历列中科院|'
    r'主简历(?:项目\d|研究成果\[)|后续表单可复用|'
    r'项目个人参与月份沿用|职责描述不等于正式项目经理任职|'
    r'不虚构(?:量化增益|正式项目经理任职)|旧解析月份和混入项目|'
    r'(?m:^confirmed_on[：:])|'
    r'"(?:confirmation_status|source_refs|confirmed_at)"\s*:'
)


def editorial_fields(kind, data):
    if not isinstance(data, dict):
        return []
    keys = ['summary'] if kind == 'basic' else ['description'] if kind == 'education' else ['description', 'responsibilities', 'results', 'abstract'] if kind == 'experience' else []
    return [key for key in keys if isinstance(data.get(key), str) and EDITORIAL.search(data[key])]


def ensure_resume_content(kind, data):
    from .app import AppError
    if editorial_fields(kind, data):
        raise AppError('resume_editorial_notes', '简历正文混入编辑或核验说明，请将说明留在整理报告或修改历史中。')
