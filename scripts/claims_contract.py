"""Rule 76 claim-template checks, shared by authoring and sealed inspection.

These are structural checks only. Exact embodiment sourcing, technical means,
complete terms and calculation-method prose remain in the existing review.
"""
import hashlib
import json
import re
from xml.etree import ElementTree as ET

VERSION = 76
BANDS = ((100, 150), (150, 200), (200, 250), (250, 300))
VAGUE = re.compile(r'该(?:系统|模块|单元|算法)|二者|三者')
RELATION = re.compile(r'[A-Za-z0-9α-ωΑ-Ω)\]）][ \t]*[=＝≤≥<>≠][ \t]*[A-Za-z0-9α-ωΑ-Ω(（+\-−]')


def enabled(version):
    return type(version) is int and version >= VERSION


def dependent_band(title, number):
    """Stable pseudorandom choice: no state, reroll or generation/review drift."""
    seed = f'claims-v76\0{title}\0{number}'.encode('utf-8')
    return BANDS[int.from_bytes(hashlib.sha256(seed).digest()[:4], 'big') % len(BANDS)]


def content(text):
    return re.sub(r'^\s*[1-9][0-9]*[.．、]\s*', '', text, count=1).strip()


def visible_count(text):
    # Engineering convention; the uploaded templates did not define counting.
    return len(re.sub(r'\s', '', content(text)))


def inventory_digest(claims):
    rows = [(c.get('number'), c.get('text'), c.get('paragraphs')) for c in claims]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def math_kind(xml_or_node):
    """Do not mistake a native symbol such as alpha for an entire formula."""
    node = ET.fromstring(xml_or_node) if isinstance(xml_or_node, str) else xml_or_node
    names = {n.tag.split('}')[-1] for n in node.iter()}
    text = ''.join(n.text or '' for n in node.iter() if n.tag.endswith('}t'))
    if (RELATION.search(text) or any(c in text for c in '=＝≠≈≡≤≥<>∑∏∫') or
            names & {'f', 'nary', 'rad'}):
        return 'formula'
    if len(text)==1 and text.isalpha() and not names & {'sSub', 'sSup', 'sSubSup', 'acc', 'd', 'func'}:
        return 'symbol'
    return 'review'


def evaluate(claims, title, body=(), artifact_sha256=None, formulas=()):
    out = dict(schema_version=1, rule_version=VERSION, artifact_sha256=artifact_sha256,
               title=title, inventory_sha256=inventory_digest(claims),
               counting='Remove claim ordinal and whitespace; count remaining visible characters including punctuation.',
               family=None, claims=[], issues=[], review_candidates=[],
               semantic_review='Existing source_support, antecedents and claims_alignment must verify the chosen source category, verbatim embodiment feature spans, complete terms, technical means only and no calculation-method description. Structural success is not semantic acceptance.')
    def issue(code, number=None, **extra):
        out['issues'].append(dict(code=code, claim=number, **extra))
    if [c.get('number') for c in claims] != list(range(1, 11)):
        issue('claim_inventory')
    if not isinstance(title, str) or not title.strip():
        issue('claim_title_unresolved')
    independents = []
    by_paragraph = {p['p']: p for p in body}
    math_by_id = {f['id']: f for f in formulas}
    for claim in claims:
        number = claim['number']
        text = content(claim['text'])
        head = re.split('[，。；：]', text, maxsplit=1)[0]
        dependent = head.startswith('根据权利要求')
        category = None
        if not dependent:
            independents.append((number, head))
            category = 'method' if head.endswith('方法') else 'system' if head.endswith('系统') else 'other'
            if not re.fullmatch(r'一种(?!一种).+', head):
                issue('independent_theme_prefix', number)
        low, high = dependent_band(title, number) if dependent else (150, 300)
        count = visible_count(claim['text'])
        row = dict(number=number, kind='dependent' if dependent else 'independent', theme=head,
                   category=category, count=count, exclusive_bounds=[low, high])
        out['claims'].append(row)
        if not low < count < high:
            issue('claim_length', number, count=count, exclusive_bounds=[low, high])
        # The ordinal dot is already removed. A decimal point is not a stop.
        if text.count('。') != 1 or not text.endswith('。'):
            issue('claim_terminal_stop', number)
        for hit in re.finditer(r'(?<!\d)\.|\.(?!\d)|[．!?！？]', text):
            out['review_candidates'].append(dict(claim=number, offset=hit.start(), text=hit[0],
                reason='Distinguish sentence punctuation from source technical names such as Node.js or .NET.'))
        if text.count('其特征在于，') != 1 or re.search('其特征在于(?!，)', text):
            issue('claim_introduction', number)
        for hit in VAGUE.finditer(text):
            issue('claim_vague_reference', number, text=hit[0], offset=hit.start())
        for hit in RELATION.finditer(text):
            issue('claim_text_formula', number, text=hit[0], offset=hit.start())
        for pnum in claim.get('paragraphs', []):
            p = by_paragraph.get(pnum, {})
            for fid in p.get('formulas', []):
                formula = math_by_id.get(fid)
                kind = math_kind(formula['omml_xml']) if formula and formula.get('omml_xml') else 'review'
                if kind=='formula':
                    issue('claim_math_object', number, paragraph=pnum, formula=fid)
                elif kind=='review':
                    out['review_candidates'].append(dict(claim=number, paragraph=pnum, formula=fid,
                        reason='Determine complete symbol versus prohibited calculation expression from original math structure and source.'))
            if p.get('media') or set(p.get('content_evidence', {}).get('objects', [])) & {'ole', 'image_or_shape'}:
                out['review_candidates'].append(dict(claim=number, paragraph=pnum,
                    reason='Inspect object visually in existing formulas/rendered_pages review; no formula may be hidden in an image or embedded object.'))
        # Cues are located for the SAME semantic review, not automatic verdicts.
        for hit in re.finditer(r'计算(?!机)|求和|加权|平方|乘以|除以|技术效果|工作原理|从而', text):
            out['review_candidates'].append(dict(claim=number, offset=hit.start(), text=hit[0],
                reason='Review technical means versus calculation description, principle or effect in source context; do not delete by keyword.'))
    nums = [n for n, _ in independents]
    heads = [head for _, head in independents]
    if nums == [1]:
        if heads[0].endswith('方法'):
            out['family'] = 'method'
        elif heads[0].endswith('系统'):
            out['family'] = 'system'
    elif nums == [1, 10]:
        if heads[0].endswith('方法') and heads[1].endswith('系统'):
            out['family'] = 'method_system'
        elif heads[0].endswith('系统') and heads[1].endswith('方法'):
            out['family'] = 'system_method'
    elif nums == [1, 9, 10] and heads[0].endswith('方法'):
        out['family'] = 'three_independent'
    if out['family'] is None:
        issue('claim_family_structure', independent_numbers=nums)
    # Source selection is NOT inferred from the title or lexical detection.
    out['family_status'] = 'detected_structure_requires_source_category_review'
    return out


def require_valid(report, expected_title):
    ev = report.get('claim_template_evidence')
    claims = report.get('claims_detected')
    if (not isinstance(claims, list) or
            [c.get('number') if isinstance(c,dict) else None for c in claims] != list(range(1,11)) or
            not isinstance(ev, dict) or
            ev.get('schema_version') != 1 or ev.get('rule_version') != VERSION or
            ev.get('artifact_sha256') != report.get('sha256') or
            not re.fullmatch('[0-9a-f]{64}', str(report.get('sha256', ''))) or
            ev.get('title') != expected_title or ev.get('inventory_sha256') != inventory_digest(claims) or
            not isinstance(ev.get('issues'), list) or
            not isinstance(ev.get('claims'), list) or
            [r.get('number') for r in ev['claims'] if isinstance(r, dict)] != list(range(1, 11)) or
            ev.get('family') not in {'method', 'system', 'method_system', 'system_method', 'three_independent'}):
        raise ValueError('Final claim-template evidence missing, stale or incomplete')
    if ev['issues']:
        raise ValueError('Final claim-template contract: '+', '.join(i['code'] for i in ev['issues'][:6]))


def is_feature_hit(report, hit):
    """Locate a hit in claim technical text or its exact solution projection.

    Merely having a source quote is insufficient: title, reference packaging,
    abstract, purpose and unrelated prose never enter this exception.
    """
    if hit.get('part')!='word/document.xml' or type(hit.get('offset')) is not int:
        return False
    body=next((p.get('paragraphs',[]) for p in report.get('parts',[]) if p.get('part')=='word/document.xml'),[])
    by_p={p['p']:p for p in body}
    target=by_p.get(hit.get('paragraph'))
    if not target or not isinstance(hit.get('text'),str):
        return False
    offset=hit['offset'];text=target['text']
    if hit.get('offset_projection')=='plain_text_with_OMML_boundary_placeholder':
        # Preferred wording offsets collapse each native math object to one
        # placeholder. Reuse already extracted spans; never reopen the DOCX.
        projection=[];positions=[];cursor=0
        for span in target.get('formula_text_spans',[]):
            start,end=span['start'],span['end']
            if not cursor<=start<=end<=len(text):return False
            projection.append(text[cursor:start]);positions.extend(range(cursor,start))
            projection.append('\ufffc');positions.append(start);cursor=end
        projection.append(text[cursor:]);positions.extend(range(cursor,len(text)))
        projected=''.join(projection)
        if offset<0 or projected[offset:offset+len(hit['text'])]!=hit['text'] or offset>=len(positions):
            return False
        offset=positions[offset]
    elif hit.get('offset_projection') not in (None,'visible_text_with_OMML_linear_text'):
        return False
    if offset<0 or text[offset:offset+len(hit['text'])]!=hit['text']:
        return False
    owner=None;source=target
    for claim in report.get('claims_detected',[]):
        if target['p'] in claim.get('paragraphs',[]):owner=claim;break
    mirror=False
    if owner is None:
        for mapping in report.get('invention_evidence',{}).get('mappings',[]):
            if mapping.get('paragraph')!=target['p']:continue
            source=by_p.get(mapping.get('source_paragraph'))
            owner=next((c for c in report.get('claims_detected',[])
                        if c.get('number')==mapping.get('claim') and source and source['p'] in c.get('paragraphs',[])),None)
            mirror=True
            break
    if owner is None or source is None:return False
    first=source['p']==owner['paragraphs'][0]
    start=0
    if first:
        marker='其特征在于，'
        if source['text'].count(marker)!=1:return False
        start=source['text'].index(marker)+len(marker)
    if mirror:
        import invention_contract
        try:projected=invention_contract.claim_head(source['text'])[2] if first else source['text']
        except ValueError:return False
        if text!=projected:return False
        start=len(projected)-len(source['text'][start:])
    return offset>=start
