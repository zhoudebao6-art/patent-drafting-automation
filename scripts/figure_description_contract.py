"""Figure-description wording only; no image-content or semantic approval."""
import re

MODE='named_figures_v32'
ENTRY=re.compile(r'图([1-9][0-9]*)为本发明的([^；。]+)([；。])')


def description(number,title,last):
    if not isinstance(title,str) or not title or re.search(r'[；。!?！？\r\n]',title):
        raise ValueError('Figure title must be a name without sentence-ending punctuation')
    if title.endswith('图图'):
        raise ValueError('Figure title has a duplicated 图 suffix')
    name=title if title.endswith('图') else title+'图'
    return f'图{number}为本发明的{name}'+('。' if last else '；')


def evidence(report,body):
    out=dict(schema_version=1,artifact_sha256=report['sha256'],complete=False,items=[],issues=[],
             scope='Only literal figure descriptions between 附图说明 and 具体实施方式; not abstract images, final captions or technical image correctness.')
    def issue(code,p=None,**extra):
        out['issues'].append(dict(code=code,part='word/document.xml',paragraph=p,**extra))
    headings={name:[p['p'] for p in body if p.get('body_direct',True) and p['text'].strip()==name]
              for name in ('附图说明','具体实施方式')}
    if any(len(rows)!=1 for rows in headings.values()):
        issue('figure_description_scope_unresolved');return out
    start,end=headings['附图说明'][0],headings['具体实施方式'][0]
    if start>=end:issue('figure_description_scope_unresolved',start);return out
    for p in body:
        if not start<p['p']<end:continue
        text=p['text'];objects=p.get('content_evidence',{}).get('objects',[])
        if not text.strip() and not objects:continue
        matches=list(ENTRY.finditer(text))
        if not matches or ''.join(m[0] for m in matches)!=text:
            issue('figure_description_unparsed',p['p']);continue
        for m in matches:
            row=dict(part='word/document.xml',paragraph=p['p'],path=f'paragraph[{p["p"]}]/visible_text',
                     offset=m.start(),number=int(m[1]),name=m[2],punctuation=m[3])
            out['items'].append(row)
            if not m[2].endswith('图') or m[2]=='图':issue('figure_description_missing_tu',p['p'],offset=m.start(),number=int(m[1]))
            elif m[2].endswith('图图'):issue('figure_description_duplicate_tu',p['p'],offset=m.start(),number=int(m[1]))
    for index,row in enumerate(out['items']):
        if row['punctuation']!=('。' if index==len(out['items'])-1 else '；'):
            issue('figure_description_punctuation',row['paragraph'],offset=row['offset'],number=row['number'])
    out['complete']=True
    return out


def validate(report):
    ev=report.get('figure_description_evidence')
    if (not isinstance(ev,dict) or type(ev.get('schema_version')) is not int or ev['schema_version']!=1 or
        ev.get('artifact_sha256')!=report.get('sha256') or not re.fullmatch('[0-9a-f]{64}',str(report.get('sha256',''))) or
        ev.get('complete') is not True or not all(isinstance(ev.get(k),list) for k in ('items','issues'))):
        raise ValueError('Final figure description evidence missing, incomplete or bound to another artifact')
    if ev['issues']:
        raise ValueError('Final figure description contract: '+', '.join(i['code'] for i in ev['issues'][:6]))
