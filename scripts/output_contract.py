"""Scoped final wording/title checks using the existing sealed inspection cache.

These objective checks supplement the six model checks, never certify semantics.
Only new batches whose frozen policy explicitly selects MODE use this contract.
"""
import re
import batch_tool as b

MODE = 'wording_title_v18'
KNOWN_YIYI = re.compile(r'一一(?:地)?(?:对应|相应|匹配|映射|配对|关联|绑定|核对|对齐|连接|比较|列出|验证|校验|检查|处理|进行|执行|标记|登记|记录|确认|说明)')


def enabled(manifest):
    return manifest.get('policy',{}).get('output_contract') == MODE


def filename(title):
    """Never silently shorten/sanitize the document's official name."""
    if (not isinstance(title,str) or not title or title != title.strip() or title.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]',title)
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',title)
            or len((title+'.docx').encode('utf-16-le'))//2 > 255):
        raise b.BatchError('Official title cannot be used unchanged as a Windows filename; resolve the title explicitly')
    return title+'.docx'


def check(report, expected_title, findings=()):
    contract=report.get('document_contract_evidence',{})
    title=contract.get('title') or {}
    if title.get('text') != expected_title:
        raise b.BatchError('Final DOCX title must exactly match the frozen official project title and delivery filename')
    if not contract.get('description_scope_resolved'):
        raise b.BatchError('Description scope unresolved; cannot certify scoped wording')
    violations=list(contract.get('description_word_hits',[]))
    for hit in contract.get('global_word_hits',[]):
        if hit['text']=='从而' or KNOWN_YIYI.search(hit['context']):
            violations.append(hit)
            continue
        # A sequence spanning two legitimate words (e.g. 统一一套流程) can
        # contain the same characters. It needs the actual independent review
        # finding, tied to the current text position; it is not a replacement.
        resolved=any(isinstance(f,dict) and f.get('rule_id')=='WORDING-04' and
                     f.get('classification')=='not_modifier' and f.get('status')=='closed' and
                     f.get('paragraph')==hit['paragraph'] and f.get('offset')==hit['offset'] and
                     f.get('part','word/document.xml')==hit.get('part','word/document.xml') and
                     f.get('text')==hit['text'] and isinstance(f.get('reason'),str) and f['reason'].strip()
                     for f in findings)
        if not resolved: violations.append(hit)
    if violations:
        where=', '.join(f'{h.get("part","word/document.xml")} P{h["paragraph"]:04}:{h["text"]}' for h in violations[:8])
        raise b.BatchError('Final wording contract not satisfied: '+where)
    return {'mode':MODE,'artifact_sha256':report['sha256'],'document_title':expected_title,
            'delivery_filename':filename(expected_title),'title_paragraph':title['paragraph'],
            'scope':'Objective title and located wording checks only; original six reviews remain required'}


def verify(m,case,attempt,receipt):
    if not enabled(m): return None
    import prepare_handoff as handoff
    # Hash-bound existing evidence; no second ZIP parse and no new model call.
    try:
        handoff.verify_prepared(m,case,attempt)
    except (ValueError, OSError, KeyError) as exc:
        raise b.BatchError('Final wording/title evidence is invalid: '+str(exc)) from exc
    prepared=b.read(attempt['prepared_handoff']['path'])
    summary=prepared['input_inspection']
    if not summary: raise b.BatchError('Final wording/title inspection is missing')
    report=b.read(summary['inspection'])
    if report.get('sha256') != attempt['input_artifact_sha256']:
        raise b.BatchError('Final wording/title report refers to another artifact')
    return check(report,case['title'],receipt.get('findings',[]))
