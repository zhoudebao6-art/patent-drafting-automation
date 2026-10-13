"""Read-only DOCX evidence extraction. Structural hints are NOT technical approval."""
import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'v': 'urn:schemas-microsoft-com:vml',
    'o': 'urn:schemas-microsoft-com:office:office',
}


def math_evidence(math):
    """Record exact glyph/structure evidence, not a count of source formulas.

    Explicit properties are reported as-is. Missing properties may be inherited;
    no guessed font, style, symbol meaning, or visual-acceptance result is added.
    """
    runs = []
    whitespace = []
    for index, run in enumerate(math.findall('.//m:r', NS), 1):
        text = ''.join(n.text or '' for n in run.findall('.//m:t', NS))
        props = run.find('./w:rPr', NS)
        fonts = props.find('./w:rFonts', NS) if props is not None else None
        size = props.find('./w:sz', NS) if props is not None else None
        mprops = run.find('./m:rPr', NS)
        values = {n.tag.split('}')[-1]: n.get(f'{{{NS["m"]}}}val')
                  for n in mprops} if mprops is not None else {}
        runs.append({'run': index, 'text': text,
                     'fonts_explicit': {k.split('}')[-1]: v for k, v in fonts.attrib.items()} if fonts is not None else {},
                     'size_half_points_explicit': size.get(f'{{{NS["w"]}}}val') if size is not None else None,
                     'math_run_properties_explicit': values})
    # Inspect every math text node, including malformed non-run text, once.
    for index, node in enumerate(math.iter(), 1):
        if node.tag not in {f'{{{NS["m"]}}}t', f'{{{NS["w"]}}}t'}:
            continue
        for offset, char in enumerate(node.text or ''):
            if char.isspace() or char in {'\u200b', '\ufeff'}:
                whitespace.append({'node': index, 'offset': offset, 'codepoint': f'U+{ord(char):04X}'})
    structures = []
    properties = []
    for node in math.iter():
        if not node.tag.startswith('{' + NS['m'] + '}'):
            continue
        name = node.tag.split('}')[-1]
        if name not in structures: structures.append(name)
        if node.attrib:
            properties.append({'element': name, 'attributes': {k.split('}')[-1]: v for k, v in node.attrib.items()}})
    return {'runs': runs, 'structure_elements': structures, 'math_properties': properties,
            'literal_whitespace': whitespace,
            'boundary': 'OMML objects include main equations, inline symbols and examples; classify against source before counting. Native operator spacing is not a literal space.'}


def visible_text(node, skip_nested_paragraphs=False):
    # Approximate accepted view; deleted/moved-from text is separately counted.
    if node.tag in {f'{{{NS["w"]}}}del', f'{{{NS["w"]}}}moveFrom'}:
        return ''
    if node.tag in {f'{{{NS["w"]}}}t', f'{{{NS["m"]}}}t'}:
        return node.text or ''
    if node.tag in {f'{{{NS["w"]}}}tab', f'{{{NS["w"]}}}br'}:
        return ' '
    if node.tag == f'{{{NS["w"]}}}sym':
        font=node.get(f'{{{NS["w"]}}}font','unknown')
        char=node.get(f'{{{NS["w"]}}}char','unknown')
        # Font-encoded glyphs need a font-aware renderer; never silently drop them.
        return f'[w:sym {font}:{char}]'
    return ''.join(visible_text(child, skip_nested_paragraphs) for child in node
                   if not (skip_nested_paragraphs and child.tag == f'{{{NS["w"]}}}p'))


def paragraph_owned_nodes(paragraph):
    """Keep all-w:p numbering, but assign descendants to their nearest w:p."""
    yield paragraph
    for child in paragraph:
        if child.tag != f'{{{NS["w"]}}}p':
            yield from paragraph_owned_nodes(child)


def parse_parent_references(head, expansion_limit=1000):
    """Bounded syntax hints only: never certify a legal dependency path."""
    refs = re.findall(r'权利要求\s*([\d、，,至到\-—–及或 \t]+)', head)
    parents = []
    issues = []
    token = r'\d+(?:\s*[至到\-—–]\s*\d+)?'
    for ref in refs:
        # A conjunction can precede another explicit 权利要求 heading.
        cleaned = ref.strip().rstrip('、，,及或').strip()
        if not re.fullmatch(token + r'(?:\s*[、，,及或]\s*' + token + r')*', cleaned):
            issues.append({'reference': ref, 'reason': 'unrecognized_reference_syntax'})
            continue
        for match in re.finditer(r'(\d+)(?:\s*[至到\-—–]\s*(\d+))?', cleaned):
            values = [s for s in match.groups() if s is not None]
            if any(len(s) > 9 for s in values):
                issues.append({'reference': ref, 'reason': 'number_too_large'})
                continue
            start = int(values[0]); end = int(values[-1])
            if end < start:
                issues.append({'reference': ref, 'reason': 'descending_range'})
                continue
            if end - start + 1 + len(parents) > expansion_limit:
                issues.append({'reference': ref, 'reason': 'expansion_limit_exceeded'})
                continue
            parents.extend(range(start, end + 1))
    return refs, list(dict.fromkeys(parents)), issues


def relationships(z, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels')
    if name not in z.namelist():
        return {}
    result = {}
    for node in ET.fromstring(z.read(name)):
        target = node.get('Target', '')
        external = node.get('TargetMode') == 'External'
        resolved = target if external else posixpath.normpath(posixpath.join(posixpath.dirname(part), target)).lstrip('/')
        result[node.get('Id')] = {'target': target, 'part': resolved, 'external': external, 'type': node.get('Type')}
    return result


def attach_section_headers(root, paragraphs, rels, z):
    """Associate body paragraphs with their section's inherited header text."""
    nodes = root.findall('.//w:p', NS)
    sections = [(i + 1, p.find('./w:pPr/w:sectPr', NS)) for i, p in enumerate(nodes)]
    sections.append((len(nodes), root.find('./w:body/w:sectPr', NS)))
    headers = {}
    start = 0
    for end, section in sections:
        if section is None:
            continue
        # Paragraph sectPr describes the section ending here, not the next one.
        # Omitted header types inherit the preceding section's reference.
        for ref in section.findall('./w:headerReference', NS):
            relation = rels.get(ref.get(f'{{{NS["r"]}}}id'), {})
            target = relation.get('part')
            text = ''
            if target in z.namelist() and not relation.get('external'):
                text = visible_text(ET.fromstring(z.read(target)))
            headers[ref.get(f'{{{NS["w"]}}}type', 'default')] = text
        for paragraph in paragraphs[start:end]:
            paragraph['section_headers'] = list(headers.values())
        start = end


def inspect(path):
    result = {'input': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'locator_convention': 'part + 1-based all w:p document order; not page numbers',
              'formula_warning': 'linear_text loses mathematical structure; use omml_xml and visual review',
              'parts': [], 'formulas': [], 'media': [], 'embeddings': [], 'issues': []}
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
            raise ValueError('Uncompressed DOCX exceeds 512 MiB inspection limit')
        names = z.namelist()
        if 'word/document.xml' not in names:
            raise ValueError('Missing word/document.xml')
        if z.testzip() is not None:
            raise ValueError('DOCX ZIP CRC failure')
        parts = [n for n in names if re.fullmatch(r'word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n)]
        parts.sort(key=lambda n: (n != 'word/document.xml', n))
        for part in parts:
            root = ET.fromstring(z.read(part))
            rels = relationships(z, part)
            paragraphs = []
            for index, p in enumerate(root.findall('.//w:p', NS), 1):
                owned_nodes = list(paragraph_owned_nodes(p))
                formula_ids = []
                for math in (n for n in owned_nodes if n.tag == f'{{{NS["m"]}}}oMath'):
                    fid = f'F{len(result["formulas"])+1:04}'
                    formula_ids.append(fid)
                    result['formulas'].append({'id':fid,'part':part,'paragraph':index,
                        'linear_text':visible_text(math),'omml_xml':ET.tostring(math,encoding='unicode'),
                        'math_evidence':math_evidence(math),
                        'status':'unverified'})
                media_refs = []
                for node in owned_nodes:
                    if node.tag == f'{{{NS["a"]}}}blip':
                        rid = node.get(f'{{{NS["r"]}}}embed') or node.get(f'{{{NS["r"]}}}link')
                    elif node.tag in {f'{{{NS["v"]}}}imagedata', f'{{{NS["o"]}}}OLEObject'}:
                        rid = node.get(f'{{{NS["r"]}}}id') or node.get(f'{{{NS["o"]}}}relid')
                    else:
                        continue
                    if not rid:
                        # WPS/Word line shapes can contain empty VML fallback imagedata.
                        # Preserve a source attribute if one exists without fetching it.
                        src = node.get('src')
                        media_refs.append({'rid':None,'legacy_source':src,'empty_shape_fallback':not bool(src)})
                        continue
                    media_refs.append({'rid':rid, **rels.get(rid, {'missing_relationship':True})})
                symbols=[{'font':n.get(f'{{{NS["w"]}}}font'),'char':n.get(f'{{{NS["w"]}}}char'),
                    'xml':ET.tostring(n,encoding='unicode')} for n in owned_nodes if n.tag == f'{{{NS["w"]}}}sym']
                paragraphs.append({'p':index, 'text':visible_text(p, skip_nested_paragraphs=True), 'formulas':formula_ids, 'media':media_refs,
                    'font_encoded_symbols':symbols})
            if part == 'word/document.xml':
                attach_section_headers(root, paragraphs, rels, z)
            revisions = {kind:len(root.findall('.//w:'+kind, NS)) for kind in ['ins','del','moveFrom','moveTo']}
            result['parts'].append({'part':part,'paragraphs':paragraphs,'relationships':rels,'revisions':revisions,
                'section_properties':[ET.tostring(s,encoding='unicode') for s in root.findall('.//w:sectPr',NS)]})
        for name in names:
            if name.startswith('word/media/') and not name.endswith('/'):
                data = z.read(name)
                result['media'].append({'part':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
            elif name.startswith('word/embeddings/') and not name.endswith('/'):
                result['embeddings'].append({'part':name,'bytes':z.getinfo(name).file_size,'status':'requires_object_review'})
    return result


def precheck(result, kind):
    issues = result['issues']
    def add(code, message, loc=None):
        issues.append({'code':code,'message':message,'locator':loc,'status':'needs_review'})
    body = next(p for p in result['parts'] if p['part']=='word/document.xml')['paragraphs']
    result['antecedent_occurrences'] = [
        {'part': 'word/document.xml', 'paragraph': p['p'], 'offset': match.start(),
         'before': p['text'][max(0, match.start()-40):match.start()],
         'following': p['text'][match.end():match.end()+80],
         'status': 'requires_complete_head_noun_and_legal_prior_basis_review'}
        for p in body for match in re.finditer('所述', p['text'])]
    result['formula_count_status'] = 'unclassified_math_objects_not_source_formula_count'
    for formula in result['formulas']:
        if formula.get('math_evidence', {}).get('literal_whitespace'):
            add('MATH_LITERAL_WHITESPACE', 'Literal whitespace inside math text; remove layout padding without altering mathematical meaning. Native math spacing is not flagged.',
                f'{formula["part"]} P{formula["paragraph"]:04} {formula["id"]}')
    if any(any(p['revisions'].values()) for p in result['parts']):
        add('REVISIONS','Tracked changes present; choose intended version before drafting. No source edits performed.')
    for part in result['parts']:
        for p in part['paragraphs']:
            if p['font_encoded_symbols']:
                add('FONT_SYMBOL','Font-encoded symbols preserved as markers; verify displayed glyphs',f'{part["part"]} P{p["p"]:04}')
            for ref in p['media']:
                if ref.get('missing_relationship'):
                    add('MISSING_REL','Media/object relationship missing',f'{part["part"]} P{p["p"]:04}')
                elif ref.get('external'):
                    add('EXTERNAL_MEDIA','External resource recorded but not fetched',f'{part["part"]} P{p["p"]:04}')
                elif ref.get('legacy_source'):
                    add('LEGACY_MEDIA','Legacy image src requires separate inspection; it was not fetched',f'{part["part"]} P{p["p"]:04}')
    if result['embeddings']:
        add('EMBEDDED_OBJECTS','Embedded objects may contain formulas; inspect them separately.')
    if not result['formulas']:
        add('NO_OMML','No OMML found. Check text, images and objects; this does not prove no formulas.')
    if kind == 'proposal':
        for title in ['技术概括','现有技术情况与问题分析','优化方案','技术实现原理']:
            if not any(title in p['text'] for p in body):
                add('SOURCE_SECTION',f'Source section not located: {title}; confirm aliases or source completeness.')
        return
    titles=['技术领域','背景技术','发明内容','附图说明','具体实施方式']
    anchors={t:next((p['p'] for p in body if p['text'].strip()==t),None) for t in titles}
    for title, anchor in anchors.items():
        if anchor is None:
            add('DRAFT_SECTION',f'Body section not located: {title}; manual confirmation needed.')
    ordered=[anchors[t] for t in titles if anchors[t] is not None]
    if ordered != sorted(ordered):
        add('SECTION_ORDER','Body headings are out of expected order.')
    # Main sections can reside in headers; do not demand duplicate body headings.
    claim_end=anchors['技术领域'] or len(body)+1
    claim_section_paragraphs={p['p'] for p in body if any(
        '权利要求书' in re.sub(r'\s+', '', text) for text in p.get('section_headers', []))}
    claims=[]
    for p in body:
        if p['p'] >= claim_end: break
        # Prefer an explicitly identified claims section; keep the body-heading
        # fallback for documents whose headers do not identify that section.
        if claim_section_paragraphs and p['p'] not in claim_section_paragraphs:
            continue
        match=re.match(r'^\s*(\d+)[.．、]\s*(.*)',p['text'])
        if match:
            claims.append({'number':int(match[1]),'p':p['p'],'text':p['text']})
        elif claims:
            claims[-1]['text']+='\n'+p['text']
    nums=[c['number'] for c in claims]
    if nums != list(range(1,len(nums)+1)) or not nums:
        add('CLAIM_NUMBERS','Claims absent, duplicated or nonconsecutive; check document layout.')
    for claim in claims:
        head=claim['text'].split('其特征在于')[0]
        refs, parents, parse_issues = parse_parent_references(head)
        claim['parents_detected']=parents
        claim['parent_reference_texts']=refs
        claim['parent_parse_issues']=parse_issues
        claim['parent_parse_status']=('requires_manual_reference_review' if parse_issues
                                      else 'structural_only_requires_legal_path_review')
        if parse_issues:
            add('CLAIM_REFERENCE_PARSE','Reference syntax/range unresolved; detected parents are incomplete and require manual review.',f'P{claim["p"]:04}')
        if any(n>=claim['number'] or n<1 or n not in nums for n in parents):
            add('CLAIM_REFERENCE','Self, future or missing parent detected',f'P{claim["p"]:04}')
    result['claims_detected']=claims
    start=anchors['附图说明']; end=anchors['具体实施方式']
    descriptions=[]; captions=[]; mentions=[]
    for p in body:
        if start and end and start<p['p']<end:
            m=re.match(r'^\s*图\s*(\d+)\s*为',p['text'])
            if m: descriptions.append(int(m[1]))
        m=re.fullmatch(r'\s*图\s*(\d+)\s*',p['text'])
        if m and end and p['p']>end: captions.append(int(m[1]))
    first_caption_p=next((p['p'] for p in body if end and p['p']>end and re.fullmatch(r'\s*图\s*\d+\s*',p['text'])),len(body)+1)
    if end:
        for p in body:
            if end<p['p']<first_caption_p:
                for num in re.findall(r'图\s*(\d+)',p['text']):
                    if int(num) not in mentions: mentions.append(int(num))
    result['figures_detected']={'descriptions':descriptions,'captions':captions,'first_mentions':mentions,
        'limitation':'Compound references such as 图1至图3 and inline captions require semantic review.'}
    if descriptions and descriptions!=list(range(1,len(descriptions)+1)):
        add('FIGURE_SEQUENCE','Description figure numbers are not consecutive.')
    if descriptions!=captions:
        add('FIGURE_CAPTIONS','Description and standalone caption sequences differ.')
    if descriptions!=mentions:
        add('FIGURE_MENTIONS','First-mention order differs from figure descriptions; verify compound refs.')
    if anchors['技术领域']:
        field=next((p['text'] for p in body if p['p']>anchors['技术领域'] and p['text'].strip()),'')
        abstract=next((p['text'] for p in body if p['text'].strip()),'')
        first=abstract.split('。')[0]+'。'
        if field.strip()!=first.strip():
            add('FIELD_ABSTRACT','Technical field does not exactly match the detected abstract first sentence.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--kind',choices=['proposal','draft'],default='draft')
    parser.add_argument('--extract-media',action='store_true')
    args=parser.parse_args()
    if args.input.suffix.lower()!='.docx':
        parser.error('Input must be DOCX; convert older formats without overwriting originals.')
    result=inspect(args.input)
    precheck(result,args.kind)
    result['review_status']='structural_precheck_only'
    result['required_semantic_reviews']=['claim antecedents and scope','claims to invention consistency',
        'formula logic, symbols, dimensions, numerical examples','figure truth and deletion dependencies',
        'source support and data provenance','final rendered pages']
    args.out.mkdir(parents=True,exist_ok=True)
    if args.extract_media:
        media_dir=args.out/'media'; media_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(args.input) as z:
            for i,entry in enumerate(result['media'],1):
                # Generated basename prevents paths from the archive escaping the output directory.
                target=media_dir/f'{i:03}_{Path(entry["part"]).name}'
                target.write_bytes(z.read(entry['part']))
                entry['extracted_path']=str(target.resolve())
    (args.out/'inspection.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=[]
    for part in result['parts']:
        lines.append('\n## '+part['part'])
        lines.extend(f'P{p["p"]:04}: {p["text"]}' for p in part['paragraphs'])
    (args.out/'content.txt').write_text('\n'.join(lines),encoding='utf-8')
    summary={'input':str(args.input),'parts':len(result['parts']),'formulas':len(result['formulas']),
        'math_objects':len(result['formulas']),'formula_count_status':result['formula_count_status'],
        'formulas_field_note':'Compatibility alias for math_objects, not the semantic source formula count.',
        'media_files':len(result['media']),'issues':len(result['issues']),'status':result['review_status']}
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    main()
