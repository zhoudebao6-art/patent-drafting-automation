"""Exact local claim-to-solution projection; never infer technical equivalence."""
from copy import deepcopy
import hashlib
import json
import re
from xml.etree import ElementTree as ET

MODE='claims_mirror_v27'
PURPOSE_END='可以有效解决上述背景技术中的问题。'
TRANSITION='为实现上述目的，本发明采取的技术方案为：'
BENEFITS='与现有技术相比，本发明的有益效果在于：'


def purpose(title):
    return '本发明的目的在于提供'+title+'，'+PURPOSE_END


def name_prefix_issue(text, title):
    """Check complete name slots, never globally remove the classifier 一种.

    Other category names and ordinary prose are deliberately not inferred from
    a composite project title. Source/claim review still checks their identity.
    """
    head=re.split('[，。；：]',re.sub(r'^[1-9][0-9]*[.．、]','',text),maxsplit=1)[0]
    if head.startswith('根据权利要求'):
        match=re.fullmatch(r'根据权利要求.+?所述的(.+)',head)
        if not match:return None
        name=match[1]
    else:
        prefix=next((p for p in ('本发明的目的在于提供','本发明提供','本发明公开','本发明涉及') if head.startswith(p)),None)
        name=head[len(prefix):] if prefix else head
    base=lambda value:re.sub(r'^(?:一种)+','',value)
    if name!=title and base(name) and base(name)==base(title):
        return dict(code='title_reference_prefix',expected=title,actual=name)
    return None


def claim_head(text):
    match=re.match(r'^([1-9][0-9]*)[.．、]',text)
    if not match:raise ValueError('Claim requires an explicit numbered first paragraph')
    body=text[match.end():]
    markers=list(re.finditer('，其特征在于，',body))
    if len(markers)!=1:raise ValueError('Claim header boundary requires located review')
    marker=markers[0];head=body[:marker.start()];tail=body[marker.end():]
    if not head or not tail:raise ValueError('Claim header or technical content is empty')
    dependent=head.startswith('根据权利要求')
    if dependent:
        if not re.fullmatch(r'根据权利要求.+所述的.+',head):
            raise ValueError('Unresolved dependent claim reference head')
        return int(match[1]), 'dependent', '优选的，'+tail
    return int(match[1]), 'independent', head+'，'+tail


def block_text(block):
    if block.get('type')!='paragraph':raise ValueError('Claim mirroring supports prose paragraphs; preserve unsupported objects on the source-bound manual route')
    if 'text' in block:return block['text']
    runs=block.get('runs')
    if not isinstance(runs,list) or not runs or any(r.get('type')!='text' for r in runs):
        raise ValueError('Claim mirroring requires text runs; never flatten formulas or other objects')
    return ''.join(r.get('text','') for r in runs)


def expand(spec):
    """Opt-in automatic generation from the current claims, before validation."""
    # This wording applies to claims even when automatic mirroring is disabled.
    # Join adjacent text runs only: never flatten a formula or another object.
    for section in spec.get('sections',[]):
        if not isinstance(section,dict) or section.get('kind')!='claims':continue
        for block in section.get('blocks',[]):
            if not isinstance(block,dict) or block.get('type')!='paragraph':continue
            segments=[block['text']] if isinstance(block.get('text'),str) else []
            if not segments:
                pending=''
                for run in block.get('runs',[]):
                    if isinstance(run,dict) and run.get('type')=='text' and isinstance(run.get('text'),str):
                        pending+=run['text']
                    else:
                        segments.append(pending);pending=''
                segments.append(pending)
            if any(re.search('其特征在于(?!，)',text) for text in segments):
                raise ValueError('Claim introduction must use 其特征在于， with a Chinese comma')
    if 'invention_from_claims' not in spec:return
    if spec.pop('invention_from_claims') is not True:
        raise ValueError('invention_from_claims must be true or omitted')
    claims=next((s['blocks'] for s in spec['sections'] if s.get('kind')=='claims'),[])
    description=next((s['blocks'] for s in spec['sections'] if s.get('kind')=='description'),[])
    headings=[i for i,b in enumerate(description) if b.get('role')=='heading' and block_text(b)=='发明内容']
    if len(headings)!=1:raise ValueError('One invention heading required for claim mirroring')
    start=headings[0]
    if start+1>=len(description) or block_text(description[start+1])!=BENEFITS:
        raise ValueError('Leave the solution region empty between invention heading and benefit introduction')
    generated=[dict(id='iv_purpose',type='paragraph',role='body',text=purpose(spec['project_title'])),
               dict(id='iv_transition',type='paragraph',role='body',text=TRANSITION)]
    numbers=[]
    for block in claims:
        if block.get('role')!='body':raise ValueError('Claim prose must use body role')
        text=block_text(block)
        if name_prefix_issue(text,spec['project_title']):
            raise ValueError('NAME-01 title_reference_prefix: use the official name unchanged in claim '+block['id'])
        if re.match(r'^[1-9][0-9]*[.．、]',text):
            number,_,converted=claim_head(text);numbers.append(number)
        elif numbers:converted=text
        else:raise ValueError('Claim content precedes the first numbered claim')
        if '权利要求' in converted or '特征在于' in converted:
            raise ValueError('Technical cross-reference needs a source-bound direct-description rewrite')
        clone=deepcopy(block)
        clone['id']='iv_'+hashlib.sha256(block['id'].encode()).hexdigest()[:20]
        if 'text' in clone:clone['text']=converted
        else:
            # Prefix alone is transformed; technical run ordering is retained.
            prefix=''
            if re.match(r'^[1-9][0-9]*[.．、]',text):
                _,kind,_=claim_head(text)
                if kind=='dependent':
                    old_end=re.search('，其特征在于，',text).end()
                    prefix='优选的，';cut_ranges=[(0,old_end)]
                else:
                    num=re.match(r'^[1-9][0-9]*[.．、]',text).end()
                    marker=re.search('其特征在于，',text)
                    cut_ranges=[(0,num),(marker.start(),marker.end())]
                offset=0;new_runs=[]
                if prefix:new_runs.append(dict(type='text',text=prefix))
                for run in clone['runs']:
                    value=run['text'];kept=''.join(c for j,c in enumerate(value,offset) if not any(a<=j<b for a,b in cut_ranges))
                    offset+=len(value)
                    if kept:new_runs.append(dict(run,text=kept))
                clone['runs']=new_runs
            assert block_text(clone)==converted
        generated.append(clone)
    if not numbers or numbers!=list(range(1,len(numbers)+1)):
        raise ValueError('Claim mirroring requires consecutive complete claims')
    description[start+1:start+1]=generated


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def math_identity(xml):
    def walk(node):
        return [node.tag,sorted(node.attrib.items()),node.text if node.tag.endswith('}t') else None,[walk(c) for c in node]]
    return digest(walk(ET.fromstring(xml)))


def evidence(report,body,claims,title):
    out=dict(schema_version=1,artifact_sha256=report['sha256'],complete=False,
             issues=[],review_candidates=[],mappings=[],scope='Current claims to invention solution; exact text after legal header conversion, paragraph boundaries and native math identity. Does not certify source technical truth or antecedents.')
    def issue(code,paragraph=None,**extra):
        out['issues'].append(dict(code=code,part='word/document.xml',paragraph=paragraph,**extra))
    if not title:issue('title_unresolved');return out
    # Also inspect name slots when claims and solution share the same mistake.
    # Reuse accepted-view paragraphs (including cross-run text), not a ZIP pass.
    for p in body:
        mismatch=name_prefix_issue(p['text'],title['text'])
        if mismatch:issue(mismatch.pop('code'),p['p'],**mismatch)
    def meaningful(p):return bool(p['text'].strip() or p.get('formulas') or p.get('media') or p.get('font_encoded_symbols') or p.get('content_evidence',{}).get('objects'))
    direct=[p for p in body if p.get('body_direct',True) and meaningful(p)]
    starts=[i for i,p in enumerate(direct) if p['p']>title['paragraph'] and p['text']=='发明内容']
    if len(starts)!=1:issue('invention_heading_unresolved');return out
    start=starts[0]
    end=next((i for i in range(start+1,len(direct)) if direct[i]['text'] in {'附图说明','具体实施方式'}),len(direct))
    benefits=[i for i in range(start+1,end) if direct[i]['text']==BENEFITS]
    if len(benefits)!=1:issue('benefit_boundary_unresolved');return out
    actual=direct[start+1:benefits[0]]
    for index,wanted in enumerate((purpose(title['text']),TRANSITION)):
        if len(actual)<=index or actual[index]['text']!=wanted:
            issue('fixed_purpose' if index==0 else 'fixed_transition',actual[index]['p'] if len(actual)>index else direct[start]['p'],expected=wanted)
    source_rows=[];expected=[];owners=[];numbers=[];unsupported={}
    for claim in claims:
        rows=[p for p in body if p['p'] in claim['paragraphs'] and p['p']<title['paragraph'] and meaningful(p)]
        if not rows:continue
        try:number,kind,first=claim_head(rows[0]['text'])
        except ValueError as exc:issue('claim_head_unresolved',rows[0]['p'],reason=str(exc));continue
        numbers.append(number)
        for i,p in enumerate(rows):
            for marker in re.finditer('其特征在于(?!，)',p['text']):
                issue('claim_intro_punctuation',p['p'],claim=number,offset=marker.start(),expected='其特征在于，')
            text=first if i==0 else p['text']
            source_rows.append(p);expected.append(text);owners.append(number)
            if '权利要求' in text or '特征在于' in text:unsupported[len(expected)-1]='technical_claim_reference'
    if not numbers or numbers!=list(range(1,len(numbers)+1)) or len(numbers)!=len(claims):issue('claim_inventory_unresolved')
    plan=actual[2:]
    if len(plan)!=len(expected):issue('solution_paragraph_count',direct[start]['p'],expected=len(expected),actual=len(plan))
    formulas={f['id']:f for f in report.get('formulas',[])}
    for i,(wanted,target,source,owner) in enumerate(zip(expected,plan,source_rows,owners)):
        row=dict(claim=owner,source_paragraph=source['p'],paragraph=target['p'],part='word/document.xml',
                 path=f'paragraph[{target["p"]}]/claim_solution',source_text_sha256=digest(source['text']),target_text_sha256=digest(target['text']))
        complex_content=any(p.get('numbering_applied') or not p.get('body_direct',True) or p.get('media') or p.get('font_encoded_symbols') or
                            set(p.get('content_evidence',{}).get('objects',[])) & {'field','ole','image_or_shape','symbol'} for p in (source,target))
        if i in unsupported or complex_content:
            out['review_candidates'].append(dict(row,reason=unsupported.get(i,'complex_claim_representation')))
        elif wanted!=target['text']:
            offset=next((j for j,(a,b) in enumerate(zip(wanted,target['text'])) if a!=b),min(len(wanted),len(target['text'])))
            issue('solution_text_changed',target['p'],claim=owner,source_paragraph=source['p'],offset=offset,
                  expected_context=wanted[max(0,offset-12):offset+45],actual_context=target['text'][max(0,offset-12):offset+45])
        try:
            sm=[math_identity(formulas[f]['omml_xml']) for f in source.get('formulas',[])]
            tm=[math_identity(formulas[f]['omml_xml']) for f in target.get('formulas',[])]
            if sm!=tm:issue('solution_math_changed',target['p'],claim=owner)
        except (KeyError,ET.ParseError):issue('solution_math_unresolved',target['p'],claim=owner)
        out['mappings'].append(row)
    # Nested text cannot silently disappear from the solution comparison.
    nested=[p for p in body if not p.get('body_direct',True) and direct[start]['p']<p['p']<direct[benefits[0]]['p'] and meaningful(p)]
    if nested:issue('nested_solution_requires_explicit_mapping',nested[0]['p'])
    out['complete']=True
    return out


def validate_evidence(report,findings):
    ev=report.get('invention_evidence')
    if (not isinstance(ev,dict) or type(ev.get('schema_version')) is not int or ev['schema_version']!=1 or
        ev.get('artifact_sha256')!=report.get('sha256') or not re.fullmatch('[0-9a-f]{64}',str(report.get('sha256',''))) or
        ev.get('complete') is not True or any(not isinstance(ev.get(k),list) for k in ('issues','mappings','review_candidates')) or not ev['mappings']):
        raise ValueError('Final invention inspection missing, incomplete or bound to another artifact')
    if ev['issues']:raise ValueError('Final invention contract: '+', '.join(str(i.get('code','malformed')) for i in ev['issues'][:6]))
    for candidate in ev['review_candidates']:
        keys=('claim','source_paragraph','paragraph','part','path','source_text_sha256','target_text_sha256')
        if not any(isinstance(f,dict) and f.get('rule_id')=='STYLE-02' and f.get('classification')=='claim_solution_mapping' and
                   f.get('status')=='closed' and f.get('artifact_sha256')==report['sha256'] and all(f.get(k)==candidate.get(k) for k in keys) and
                   isinstance(f.get('reason'),str) and f['reason'].strip() for f in findings):
            raise ValueError('Final invention mapping requires located independent review: '+candidate['path'])
