"""Read-only DOCX evidence extraction. Structural hints are NOT technical approval."""
import argparse
import hashlib
import json
import posixpath
import re
import stat
import uuid
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


def explicit_properties(node):
    """Retain direct properties only; absence does not mean a default value."""
    if node is None:
        return None
    return [{'name': child.tag.split('}')[-1],
             'attributes': {k.split('}')[-1]: v for k, v in child.attrib.items()}}
            for child in node]


def math_layout_evidence(math):
    """Locate layout risks without flattening or rewriting mathematical trees."""
    arrays, gaps, breaks, conflicts = [], [], [], []
    required = {'f': ('num', 'den'), 'sSub': ('e', 'sub'),
                'sSup': ('e', 'sup'), 'sSubSup': ('e', 'sub', 'sup'),
                'acc': ('e',), 'bar': ('e',), 'nary': ('e',),
                'limLow': ('e', 'lim'), 'limUpp': ('e', 'lim')}
    def meaningful(node):
        return bool(node is not None and (visible_text(node).strip() or
                    node.find('.//w:sym', NS) is not None))
    def walk(node, path):
        name = node.tag.split('}')[-1]
        if node.tag.startswith('{' + NS['m'] + '}'):
            if name == 'eqArr':
                rows = node.findall('./m:e', NS)
                arrays.append({'path': path, 'top_level': node in list(math),
                               'row_count': len(rows),
                               'rows': [{'row': i, 'linear_text': visible_text(row),
                                         'trailing_operator': bool(re.search(r'[=≠≤≥<>≈≔+−×÷]$', visible_text(row).rstrip()))}
                                        for i, row in enumerate(rows, 1)]})
            for slot in required.get(name, ()):
                if not meaningful(node.find('./m:' + slot, NS)):
                    gaps.append({'path': path, 'element': name, 'slot': slot})
        if node.tag in {f'{{{NS["m"]}}}brk', f'{{{NS["w"]}}}br', f'{{{NS["w"]}}}tab'}:
            breaks.append({'path': path, 'element': name})
        if node.tag == f'{{{NS["m"]}}}r':
            sty = node.find('./m:rPr/m:sty', NS)
            style = sty.get(f'{{{NS["m"]}}}val') if sty is not None else None
            # Conflicting direct hints vary by renderer. Never erase semantic bold.
            for prop, styles in [('b', {'p', 'i'}), ('i', {'p', 'b'})]:
                wp = node.find('./w:rPr/w:' + prop, NS)
                if wp is not None and wp.get(f'{{{NS["w"]}}}val', '1') not in {'0', 'false', 'off'} and style in styles:
                    conflicts.append({'path': path, 'text': visible_text(node),
                                      'math_style': style, 'word_property': prop})
        counters = {}
        for child in node:
            tag = child.tag.split('}')[-1]
            counters[tag] = counters.get(tag, 0) + 1
            walk(child, f'{path}/{tag}[{counters[tag]}]')
    walk(math, 'oMath')
    return {'equation_arrays': arrays, 'empty_required_slots': gaps,
            'explicit_breaks_or_tabs': breaks, 'conflicting_run_properties': conflicts,
            'boundary': 'Arrays can encode conditions, matrices or complete equations. These are review cues, not permission to flatten, invent operands, or certify visual fit.'}


def paragraph_layout_evidence(paragraph):
    props = paragraph.find('./w:pPr', NS)
    def attributes(name):
        node = props.find('./w:' + name, NS) if props is not None else None
        return {k.split('}')[-1]: v for k, v in node.attrib.items()} if node is not None else None
    def outside_math(node):
        if node.tag in {f'{{{NS["m"]}}}oMath', f'{{{NS["w"]}}}del', f'{{{NS["w"]}}}moveFrom'}:
            return ''
        if node.tag == f'{{{NS["w"]}}}t':
            return node.text or ''
        return ''.join(outside_math(c) for c in node if c.tag != f'{{{NS["w"]}}}p')
    return {'paragraph_style_explicit': attributes('pStyle'),
            'alignment_explicit': attributes('jc'), 'indent_explicit': attributes('ind'),
            'spacing_explicit': attributes('spacing'),
            'math_only_text_candidate': not outside_math(paragraph).strip(' \t\r\n；;。.'),
            'paragraph_properties_xml': ET.tostring(props, encoding='unicode') if props is not None else None,
            'boundary': 'Direct properties only; resolve style inheritance, section/table width and renderer output before deciding fit or clipping.'}


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
                     'word_run_properties_explicit': explicit_properties(props),
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
            'layout': math_layout_evidence(math),
            'literal_whitespace': whitespace,
            'boundary': 'OMML objects include main equations, inline symbols and examples; classify against source before counting. Native operator spacing is not a literal space.'}


def word_run_formats(paragraph):
    """Compact direct Word text formatting; nested text boxes own their runs."""
    groups = {}
    for node in paragraph_owned_nodes(paragraph):
        if node.tag != f'{{{NS["w"]}}}r':
            continue
        text = visible_text(node, skip_nested_paragraphs=True)
        if not text:
            continue
        props = explicit_properties(node.find('./w:rPr', NS))
        key = json.dumps(props, sort_keys=True)
        if key not in groups:
            groups[key] = {'properties_explicit': props, 'run_count': 0, 'samples': []}
        row = groups[key]
        row['run_count'] += 1
        if len(row['samples']) < 3:
            row['samples'].append(text[:80])
    return list(groups.values())


def isolated_symbol_candidate(math):
    """Only a bare single letter; never flatten scripts, accents or operators."""
    children = [n for n in math if n.tag != f'{{{NS["m"]}}}oMathPr']
    if len(children) != 1 or children[0].tag != f'{{{NS["m"]}}}r':
        return False
    run = children[0]
    allowed = {f'{{{NS["m"]}}}{n}' for n in ['t', 'rPr']} | {f'{{{NS["w"]}}}rPr'}
    if any(n.tag not in allowed for n in run):
        return False
    text = visible_text(math)
    return len(text) == 1 and text.isalpha() and not ('\u3400' <= text <= '\u9fff')


def wording_evidence(body, claims):
    """Locate edits by scope; never replace titles or certify antecedents."""
    rows = []
    claim_by_p = {p: c['number'] for c in claims for p in c['paragraphs']}
    for p in body:
        text = p['text']
        claim = claim_by_p.get(p['p'])
        for m in re.finditer('本技术项目|本方案|本方法|本申请|装置|组件|引擎', text):
            token = m[0]
            rows.append({'paragraph': p['p'], 'offset': m.start(), 'text': token,
                         'context': text[max(0,m.start()-24):m.end()+32], 'claim': claim,
                         'category': 'self_reference' if token.startswith('本') else 'module_term',
                         'replacement': '本发明' if token.startswith('本') else '模块',
                         'status': 'review_scope_and_protected_name_before_edit'})
        if claim is not None:
            # 所有“较/等”保留线索，比较/等于/等级等完整词由语义判别排除。
            for m in re.finditer('邻近的?|较|等', text):
                token = m[0]
                rows.append({'paragraph': p['p'], 'offset': m.start(), 'text': token,
                             'context': text[max(0,m.start()-24):m.end()+32], 'claim': claim,
                             'category': 'claim_near' if token.startswith('邻近') else 'claim_qualifier_or_enumeration',
                             'replacement': '相邻' if token.startswith('邻近') else None,
                             'status': 'semantic_classification_required_not_character_deletion'})
    return {'occurrences': rows,
            'boundary': 'Claims-only neighbor/comparative qualifier/open enumeration rules do not apply elsewhere. Module terms require protected names/titles to remain unchanged. This is read-only candidate evidence; 等于/相等/等级 and 比较 are not automatically deleted. Check images/fields not represented as ordinary text separately.'}


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


def color_reader(z):
    """Resolve common applied Word font colors, not unused style declarations.

    Conditional table formatting, drawing text and rendering remain visual checks.
    Unknown themes/styles are review cues, never evidence that text is black.
    """
    w = '{' + NS['w'] + '}'
    styles = ET.fromstring(z.read('word/styles.xml')) if 'word/styles.xml' in z.namelist() else ET.Element('styles')
    catalog = {s.get(w+'styleId'): s for s in styles.findall('w:style', NS)}
    defaults = {s.get(w+'type'): s.get(w+'styleId') for s in catalog.values() if s.get(w+'default') in {'1','true','on'}}
    default_color = styles.find('./w:docDefaults/w:rPrDefault/w:rPr/w:color', NS)
    theme = {}
    theme_parts = [r['part'] for r in relationships(z, 'word/document.xml').values()
                   if not r.get('external') and r.get('type', '').endswith('/theme')]
    if len(theme_parts) == 1 and theme_parts[0] in z.namelist():
        tree = ET.fromstring(z.read(theme_parts[0]))
        for slot in tree.findall('./a:themeElements/a:clrScheme/*', NS):
            if len(slot): theme[slot.tag.split('}')[-1]] = slot[0].get('val') if slot[0].tag.endswith('}srgbClr') else slot[0].get('lastClr')
    aliases = {'dark1':'dk1','light1':'lt1','dark2':'dk2','light2':'lt2','hyperlink':'hlink','followedHyperlink':'folHlink'}
    cache = {}
    def style_color(key, trail=()):
        if not key: return None, None
        if key in cache: return cache[key]
        if key in trail: return None, 'style_cycle:'+key
        node = catalog.get(key)
        if node is None: return None, 'missing_style:'+key
        color = node.find('./w:rPr/w:color', NS)
        parent = node.find('w:basedOn', NS)
        answer = (color, None) if color is not None else style_color(parent.get(w+'val') if parent is not None else None, trail+(key,))
        cache[key] = answer
        return answer
    def read(p, owned, part, number):
        pstyle = p.find('./w:pPr/w:pStyle', NS)
        pid = pstyle.get(w+'val') if pstyle is not None else defaults.get('paragraph')
        rows = []
        for index, run in enumerate((x for x in owned if x.tag in {w+'r', '{'+NS['m']+'}r'}), 1):
            text = visible_text(run, skip_nested_paragraphs=True)
            if not text.strip(): continue
            style = run.find('./w:rPr/w:rStyle', NS)
            sid = style.get(w+'val') if style is not None else defaults.get('character')
            color, unresolved = default_color, []
            origin = 'docDefaults' if color is not None else 'automatic'
            for key in (pid, sid):
                inherited, problem = style_color(key)
                if problem: unresolved.append(problem)
                if inherited is not None: color, origin, unresolved = inherited, 'style:'+key, []
            direct = run.find('./w:rPr/w:color', NS)
            if direct is not None: color, origin, unresolved = direct, 'direct', []
            attrs = {k.split('}')[-1]:v for k,v in color.attrib.items()} if color is not None else {}
            rgb = attrs.get('val', 'auto').upper()
            if attrs.get('themeColor'):
                key = aliases.get(attrs['themeColor'], attrs['themeColor'])
                rgb = theme.get(key)
                rgb = rgb.upper() if isinstance(rgb,str) else None
                if rgb is None: unresolved.append('unresolved_theme_color')
            if attrs.get('themeTint') or attrs.get('themeShade'):
                unresolved.append('theme_tint_or_shade_requires_display_check')
            if any(n.tag.endswith('}textFill') for n in run.iter()):
                unresolved.append('text_fill_requires_display_check')
            if rgb not in {'AUTO', None} and not re.fullmatch('[0-9A-F]{6}', rgb):
                unresolved.append('invalid_rgb'); rgb = None
            if unresolved or rgb not in {'000000','AUTO',None}:
                rows.append({'part':part,'paragraph':number,'run':index,'text':text[:120],
                             'origin':origin,'paragraph_style':pid,'character_style':sid,
                             'color':attrs,'resolved_rgb':rgb,'unresolved':unresolved})
        return rows
    return read


def inspect(path):
    result = {'input': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'locator_convention': 'part + 1-based all w:p document order; not page numbers',
              'formula_warning': 'linear_text loses mathematical structure; use omml_xml and visual review',
              'parts': [], 'formulas': [], 'media': [], 'embeddings': [], 'issues': [],
              'text_color_evidence': {'review_runs': [],
                  'scope': 'Applied document-default, paragraph/character basedOn and direct Word/OMML run font colors. Unused styles ignored. Automatic colors, table conditional formatting, DrawingML/image text, highlighting and revision display still require final visual review.'}}
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
            raise ValueError('Uncompressed DOCX exceeds 512 MiB inspection limit')
        names = z.namelist()
        if 'word/document.xml' not in names:
            raise ValueError('Missing word/document.xml')
        if z.testzip() is not None:
            raise ValueError('DOCX ZIP CRC failure')
        read_colors = color_reader(z)
        # Keep compact style definitions so paragraph/header inheritance can be
        # resolved without reopening the ZIP. This is not an effective-style verdict.
        if 'word/styles.xml' in names:
            styles_root = ET.fromstring(z.read('word/styles.xml'))
            default = styles_root.find('./w:docDefaults', NS)
            result['style_evidence'] = {
                'doc_defaults_xml': ET.tostring(default, encoding='unicode') if default is not None else None,
                'styles': [{'id': s.get(f'{{{NS["w"]}}}styleId'),
                            'type': s.get(f'{{{NS["w"]}}}type'),
                            'default': s.get(f'{{{NS["w"]}}}default'),
                            'xml': ET.tostring(s, encoding='unicode')}
                           for s in styles_root.findall('./w:style', NS)],
                'boundary': 'Definitions, not applied formatting. Resolve actual styles, inheritance, direct overrides and final renderer output.'}
        parts = [n for n in names if re.fullmatch(r'word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n)]
        parts.sort(key=lambda n: (n != 'word/document.xml', n))
        for part in parts:
            root = ET.fromstring(z.read(part))
            rels = relationships(z, part)
            paragraphs = []
            for index, p in enumerate(root.findall('.//w:p', NS), 1):
                owned_nodes = list(paragraph_owned_nodes(p))
                result['text_color_evidence']['review_runs'].extend(read_colors(p, owned_nodes, part, index))
                formula_ids = []
                for math in (n for n in owned_nodes if n.tag == f'{{{NS["m"]}}}oMath'):
                    fid = f'F{len(result["formulas"])+1:04}'
                    formula_ids.append(fid)
                    result['formulas'].append({'id':fid,'part':part,'paragraph':index,
                        'linear_text':visible_text(math),'omml_xml':ET.tostring(math,encoding='unicode'),
                        'math_evidence':math_evidence(math),
                        'isolated_symbol_candidate':isolated_symbol_candidate(math),
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
                    'font_encoded_symbols':symbols,
                    'word_run_formats':word_run_formats(p),
                    'layout_evidence':paragraph_layout_evidence(p)})
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
    for row in result.get('text_color_evidence', {}).get('review_runs', []):
        add('TEXT_COLOR_REVIEW', 'Applied nonblack or unresolved font color; final patent prose and explanations must use clean black text. Resolve inherited/theme formatting and verify display; do not delete technical content.',
            f'{row["part"]} P{row["paragraph"]:04} run{row["run"]}')
    for formula in result['formulas']:
        locator = f'{formula["part"]} P{formula["paragraph"]:04} {formula["id"]}'
        layout = formula.get('math_evidence', {}).get('layout', {})
        for array in layout.get('equation_arrays', []):
            if array['top_level'] and any(row['trailing_operator'] for row in array['rows'][:-1]):
                add('MATH_ARRAY_TRAILING_OPERATOR', 'Top-level equation array has a row ending at an operator. Check source semantics and reflow display-only breaks; preserve conditional and multi-equation structures.', locator + ' ' + array['path'])
        for key, code in [('empty_required_slots', 'MATH_EMPTY_SLOT'),
                          ('explicit_breaks_or_tabs', 'MATH_EXPLICIT_BREAK'),
                          ('conflicting_run_properties', 'MATH_STYLE_CONFLICT')]:
            if layout.get(key):
                add(code, 'Math structure or direct formatting needs source and visual review; details in math_evidence.layout. Do not auto-correct symbols.', locator)
        if formula.get('math_evidence', {}).get('literal_whitespace'):
            add('MATH_LITERAL_WHITESPACE', 'Literal whitespace inside math text; remove layout padding without altering mathematical meaning. Native math spacing is not flagged.',
                f'{formula["part"]} P{formula["paragraph"]:04} {formula["id"]}')
    if any(any(p['revisions'].values()) for p in result['parts']):
        add('REVISIONS','Tracked changes present; choose intended version before drafting. No source edits performed.')
    for part in result['parts']:
        for p in part['paragraphs']:
            layout = p.get('layout_evidence') or {}
            if p['formulas'] and (layout.get('spacing_explicit') or {}).get('lineRule') == 'exact':
                add('MATH_FIXED_LINE_HEIGHT', 'Exact line height may clip tall inline or display math; resolve inheritance and inspect actual glyph bounds.', f'{part["part"]} P{p["p"]:04}')
            indent = layout.get('indent_explicit') or {}
            if p['formulas'] and layout.get('math_only_text_candidate') and any(indent.get(k, '0') != '0' for k in ['firstLine', 'firstLineChars', 'hanging', 'hangingChars']):
                add('MATH_DISPLAY_INDENT', 'Math-only paragraph carries an explicit first-line/hanging indent. Review display centering separately from prose.', f'{part["part"]} P{p["p"]:04}')
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
            claims.append({'number':int(match[1]),'p':p['p'],'text':p['text'], 'paragraphs':[p['p']]})
        elif claims:
            claims[-1]['text']+='\n'+p['text']
            claims[-1]['paragraphs'].append(p['p'])
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
        claim['antecedent_count_evidence'] = {
            'literal_count':claim['text'].count('所述'),
            'reference_head_count':len(re.findall(r'根据权利要求[^。；\n]*?所述',head)),
            'required_valid_minimum':3,
            'applicability':'Rule version 16 onward; earlier frozen batches keep their recorded requirements.',
            'status':'requires_each_occurrence_validity_review_not_automatic_pass'}
        if claim['text'].count('所述') < 3:
            add('CLAIM_SHUSU_MINIMUM','Each whole claim needs at least 3 valid 所述. Rephrase existing source-backed references; never prefix first-use nouns or invent features to meet a count.',f'P{claim["p"]:04}')
        if parse_issues:
            add('CLAIM_REFERENCE_PARSE','Reference syntax/range unresolved; detected parents are incomplete and require manual review.',f'P{claim["p"]:04}')
        if any(n>=claim['number'] or n<1 or n not in nums for n in parents):
            add('CLAIM_REFERENCE','Self, future or missing parent detected',f'P{claim["p"]:04}')
    result['claims_detected']=claims
    result['wording_evidence']=wording_evidence(body, claims)
    if result['wording_evidence']['occurrences']:
        add('WORDING_SCOPE_REVIEW','Review located wording candidates against claim-only restrictions, protected names/titles and whole-body replacements; never perform blind character replacement.')
    result['isolated_symbol_evidence']=[{'id':f['id'],'part':f['part'],'paragraph':f['paragraph'],'symbol':f['linear_text'],
        'status':'may_use_plain_upright_body_run_only_if_registered_bare_symbol_no_semantic_style'}
        for f in result['formulas'] if f.get('isolated_symbol_candidate')]
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
    result['figure_placement_evidence'] = {
        'image_occurrences': [{'paragraph':p['p'], 'section_headers':p.get('section_headers', []),
                               'media':p['media']} for p in body if p['media']],
        'description_paragraphs': [{'paragraph':p['p'],'text':p['text']} for p in body
                                   if start and end and start<p['p']<end],
        'reference_paragraphs': [{'paragraph':p['p'],'text':p['text']} for p in body
                                 if end and end<p['p']<first_caption_p and re.search(r'图\s*\d+', p['text'])],
        'caption_paragraphs': [{'paragraph':p['p'],'text':p['text']} for p in body
                               if end and p['p']>end and re.fullmatch(r'\s*图\s*\d+\s*', p['text'])],
        'boundary': 'Relationship/document order is not visual page placement or semantic figure identity. Verify first-mention -> description -> final image -> caption mapping; distinguish abstract reuse and formula images; keep image and its own caption on the same page.'}
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


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def local_path(path):
    """Reject links/reparse points, including ancestors, before resolving paths."""
    path = Path(path).absolute()
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked/reparse inspection path is not allowed: ' + str(node))
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Hard-linked inspection file is not allowed: ' + str(node))
    return path.resolve()


def inspection_summary(result, output, cache_status, cache_reason):
    return {'input': result['input'], 'parts': len(result['parts']),
        'formulas': len(result['formulas']), 'math_objects': len(result['formulas']),
        'formula_count_status': result['formula_count_status'],
        'formulas_field_note': 'Compatibility alias for math_objects, not the semantic source formula count.',
        'media_files': len(result['media']), 'issues': len(result['issues']),
        'status': result['review_status'], 'cache_status': cache_status, 'cache_reason': cache_reason,
        'output_directory': str(output), 'inspection': str(output/'inspection.json'),
        'content': str(output/'content.txt')}


def verified_cache(directory, identity):
    """Verify derived evidence bytes without reopening/parsing the source ZIP."""
    directory = local_path(directory)
    metadata = json.loads(local_path(directory/'cache.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict):
        raise ValueError('Cache metadata object required')
    if metadata.get('schema_version') != 1 or metadata.get('identity') != identity:
        raise ValueError('Cache identity mismatch')
    records = metadata['outputs']
    if not isinstance(records, list):
        raise ValueError('Cache output list required')
    files = {}
    for row in records:
        relative = Path(row['file'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Cache artifact escaped generation')
        target = local_path(directory/relative)
        if not target.is_relative_to(directory) or target in files:
            raise ValueError('Duplicate or foreign cache artifact')
        if file_sha(target) != row['sha256']:
            raise ValueError('Cache artifact changed: ' + row['file'])
        files[target] = row['sha256']
    report = directory/'inspection.json'
    if report not in files or directory/'content.txt' not in files:
        raise ValueError('Cache report/content missing')
    result = json.loads(report.read_text(encoding='utf-8'))
    if not isinstance(result, dict):
        raise ValueError('Cache report object required')
    if (result.get('input') != identity['input'] or result.get('sha256') != identity['source_sha256']
            or result.get('review_status') != 'structural_precheck_only'):
        raise ValueError('Cache report source/status mismatch')
    expected = {report, directory/'content.txt'}
    if identity['extract_media']:
        for index, entry in enumerate(result['media'], 1):
            target = directory/'media'/f'{index:03}_{Path(entry["part"]).name}'
            if entry.get('extracted_path') != str(target) or files.get(target) != entry['sha256']:
                raise ValueError('Cache extracted media mismatch')
            expected.add(target)
    if set(files) != expected:
        raise ValueError('Cache artifact inventory mismatch')
    return result


def cache_identity(input_path, kind, extract_media):
    source = local_path(input_path)
    return {'input': str(source), 'source_sha256': file_sha(source),
            'extractor_sha256': file_sha(Path(__file__).resolve()),
            'kind': kind, 'extract_media': bool(extract_media)}


def cache_key(identity):
    # Short directory names avoid Windows MAX_PATH failures under case/attempt.
    # Full source/tool hashes in cache.json remain authoritative; a key collision
    # can share a bucket but cannot pass verified_cache with another identity.
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def run_inspection(input_path, output, kind='draft', extract_media=False, reuse=False):
    """An explicit case-local cache stores structural evidence, never review approval."""
    source = local_path(input_path)
    output = local_path(output)
    if source.suffix.lower() != '.docx' or kind not in {'proposal', 'draft'}:
        raise ValueError('A DOCX and proposal/draft kind are required')
    extractor = Path(__file__).resolve()
    identity = cache_identity(source, kind, extract_media)
    cache_reason = 'reuse_not_requested'
    if reuse:
        key = cache_key(identity)
        bucket = local_path(output/key)
        bucket.mkdir(parents=True, exist_ok=True)
        candidates = sorted(bucket.glob('generation-*'))
        cache_reason = 'new_identity' if not candidates else 'no_valid_generation'
        for candidate in candidates:
            try:
                result = verified_cache(candidate, identity)
                summary = inspection_summary(result, candidate, 'hit', 'verified_all_artifacts')
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Preserve incomplete or damaged evidence; never overwrite it.
            if file_sha(source) != identity['source_sha256'] or file_sha(extractor) != identity['extractor_sha256']:
                raise ValueError('Source or extractor changed during cache verification')
            return summary
        output = bucket/('generation-' + uuid.uuid4().hex[:16])
        output.mkdir(exist_ok=False)
    else:
        output.mkdir(parents=True, exist_ok=True)
    result = inspect(source)
    precheck(result, kind)
    result['review_status']='structural_precheck_only'
    result['required_semantic_reviews']=['claim antecedents and scope','claims to invention consistency',
        'formula logic, symbols, dimensions, numerical examples','figure truth and deletion dependencies',
        'source support and data provenance','final rendered pages']
    artifacts = []
    if extract_media:
        media_dir=local_path(output/'media'); media_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(source) as z:
            for i,entry in enumerate(result['media'],1):
                # Generated basename prevents paths from the archive escaping the output directory.
                target=local_path(media_dir/f'{i:03}_{Path(entry["part"]).name}')
                target.write_bytes(z.read(entry['part']))
                entry['extracted_path']=str(target.resolve())
                artifacts.append(target)
    if result['sha256'] != identity['source_sha256'] or file_sha(source) != identity['source_sha256']:
        raise ValueError('Source changed during inspection; no reusable cache committed')
    report = local_path(output/'inspection.json')
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=[]
    for part in result['parts']:
        lines.append('\n## '+part['part'])
        lines.extend(f'P{p["p"]:04}: {p["text"]}' for p in part['paragraphs'])
    content = local_path(output/'content.txt')
    content.write_text('\n'.join(lines),encoding='utf-8')
    if file_sha(source) != identity['source_sha256'] or file_sha(extractor) != identity['extractor_sha256']:
        raise ValueError('Source or extractor changed during inspection; no reusable cache committed')
    if reuse:
        artifacts.extend([report, content])
        metadata = {'schema_version': 1, 'identity': identity,
                    'outputs': [{'file': str(p.relative_to(output)), 'sha256': file_sha(p)} for p in artifacts]}
        (output/'cache.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        verified_cache(output, identity)
    return inspection_summary(result, output, 'miss' if reuse else 'disabled', cache_reason)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--kind',choices=['proposal','draft'],default='draft')
    parser.add_argument('--extract-media',action='store_true')
    parser.add_argument('--reuse', action='store_true', help='Use --out as a case-local, hash-keyed evidence cache')
    args=parser.parse_args()
    print(json.dumps(run_inspection(args.input, args.out, args.kind, args.extract_media, args.reuse), ensure_ascii=False))


if __name__=='__main__':
    main()
