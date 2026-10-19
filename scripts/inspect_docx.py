"""Read-only DOCX evidence extraction. Structural hints are NOT technical approval."""
import argparse
import hashlib
import json
import posixpath
import re
import stat
import unicodedata
import uuid
import zipfile
import single_line_math
import formula_uniqueness
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

# Displayed characters only: XML punctuation and diagnostic markers are not prose.
PUNCTUATION_EXTRA = frozenset('\"\'＂＇❛❜❝❞❟❠🙶🙷🙸' + ''.join(chr(n) for a,b in
    [(0x239B,0x23AD),(0x23B0,0x23B1),(0x23B4,0x23B6),(0x23DC,0x23E1)] for n in range(a,b+1)))
PREFERRED_WORDING={'可选的':'优选的','配置为':'用于','正文中':'文中','正文':'文中','源文件':'文中','一一对应':'都相对应'}
PREFERRED_PATTERN=re.compile('|'.join(map(re.escape,PREFERRED_WORDING)))


def forbidden_punctuation(char):
    # Compatibility-enclosed forms such as ⑴ visibly contain parentheses too.
    return any(c in PUNCTUATION_EXTRA or unicodedata.category(c) in {'Ps','Pe','Pi','Pf'}
               for c in char + unicodedata.normalize('NFKC',char))


def literal_content_whitespace(char):
    """Literal content padding, not mathematical operator or font spacing."""
    return char.isspace() or char in {'\u200b', '\ufeff'}


def content_spacing_hits(root, part):
    """Scan each parsed part once, including nested text boxes without repeats.

    Word field instructions and property trees are not displayed content. Field
    results, current insertions, OMML text and DrawingML/chart labels are content.
    Pictures, font-encoded glyphs and rendered/generated labels remain covered by
    the existing independent source and final-page review.
    """
    w, m, a = (f'{{{NS[key]}}}' for key in ('w', 'm', 'a'))
    chart_values = {'{http://schemas.openxmlformats.org/drawingml/2006/chart}v',
                    '{http://schemas.microsoft.com/office/drawing/2014/chartex}v'}
    paragraphs = {id(p): i for i, p in enumerate(root.findall('.//w:p', NS), 1)}
    hits = []
    def add(text, path, paragraph, representation, math):
        for offset, char in enumerate(text):
            if literal_content_whitespace(char):
                hits.append({'part': part, 'paragraph': paragraph, 'path': path,
                             'offset': offset, 'text': char, 'codepoint': f'U+{ord(char):04X}',
                             'representation': representation, 'in_formula': math,
                             'context': text[max(0, offset-20):offset+21]})
    def walk(node, path, paragraph=None, math=False):
        # Del/moveFrom can contain nested paragraphs. Their ordinal positions
        # remain in the locator map, but their removed contents are not scanned.
        if node.tag in {w+'del', w+'moveFrom', w+'pPr', w+'rPr', w+'tabs', w+'instrText'}:
            return
        paragraph = paragraphs.get(id(node), paragraph)
        math = math or node.tag == m+'oMath'
        if node.tag in {w+'t', m+'t', a+'t'}:
            representation = 'math' if math else ('drawing_text' if node.tag == a+'t' else 'text')
            add(node.text or '', path, paragraph, representation, math)
        elif part.startswith('word/charts/') and node.tag in chart_values:
            add(node.text or '', path, paragraph, 'chart_text', math)
        elif node.tag == f'{{{NS["v"]}}}textpath':
            add(node.get('string', ''), path, paragraph, 'vml_text', math)
        elif node.tag == a+'buChar':
            add(node.get('char', ''), path, paragraph, 'drawing_bullet', math)
        elif node.tag == w+'tab':
            # w:tab inside w:pPr/w:tabs was excluded with the property subtree.
            add('\t', path, paragraph, 'text_tab', math)
        counts = {}
        for child in node:
            name = child.tag.split('}')[-1]
            counts[name] = counts.get(name, 0) + 1
            walk(child, f'{path}/{name}[{counts[name]}]', paragraph, math)
    walk(root, root.tag.split('}')[-1])
    return hits


def punctuation_evidence(root, part):
    """Locate real characters and implicit math fences without rewriting source.

    Prime and comparison operators are not quotation/bracket punctuation. Font
    encoded glyphs, fields, pictures and generated numbering still need review.
    """
    hits=[]; formula_hits=[]; candidates=[]; texts={}
    paragraphs={id(p):i for i,p in enumerate(root.findall('.//w:p',NS),1)}
    text_tags={f'{{{NS[n]}}}t' for n in ('w','m','a')}
    cns='http://schemas.openxmlformats.org/drawingml/2006/chart'
    def add(text,location,force=False):
        for offset,ch in enumerate(text):
            if forbidden_punctuation(ch) or (force and ch in '<>'):
                target=formula_hits if location['in_formula'] else hits
                target.append(dict(location,offset=offset,text=ch,codepoint=f'U+{ord(ch):04X}',
                                 context=text[max(0,offset-16):offset+17]))
    def walk(node,path,p=0,math=False,group=None):
        if node.tag in {f'{{{NS["w"]}}}del',f'{{{NS["w"]}}}moveFrom'}:return
        if node.tag==f'{{{NS["m"]}}}oMath' and not math and group is not None:
            # Do not join plain words on opposite sides of a preserved formula.
            texts.setdefault((p,group),[]).append('\ufffc')
        if not math and group is not None:
            if node.tag in {f'{{{NS["w"]}}}{n}' for n in ('tab','br','cr')} | {f'{{{NS["a"]}}}br'}:
                texts.setdefault((p,group),[]).append(' ')
            elif node.tag in {f'{{{NS["w"]}}}sym',f'{{{NS["a"]}}}blip',f'{{{NS["o"]}}}OLEObject',f'{{{NS["v"]}}}imagedata'}:
                texts.setdefault((p,group),[]).append('\ufffc')
        p=paragraphs.get(id(node),p);math=math or node.tag==f'{{{NS["m"]}}}oMath'
        if node.tag in {f'{{{NS["w"]}}}p',f'{{{NS["a"]}}}p'}:group=path
        loc={'part':part,'paragraph':p,'path':path,'representation':'math' if math else 'text','in_formula':math}
        if node.tag in text_tags or (part.startswith('word/charts/') and node.tag==f'{{{cns}}}v'):
            add(node.text or '',loc)
            if not math:
                key=(p,group or path);texts.setdefault(key,[]).append(node.text or '')
            if not math and any(ch in (node.text or '') for ch in '<>'):
                candidates.append(dict(loc,reason='ascii_angle_use_requires_semantic_review'))
        if node.tag==f'{{{NS["v"]}}}textpath':
            add(node.get('string',''),dict(loc,representation='vml_text'))
            texts.setdefault((p,path),[]).append(node.get('string',''))
        if node.tag==f'{{{NS["a"]}}}buChar':
            add(node.get('char',''),dict(loc,representation='drawing_bullet'))
        if node.tag==f'{{{NS["m"]}}}d':
            # Absent properties default to parentheses; an explicit empty
            # property is an invisible delimiter, including a missing val.
            for name,default in [('begChr','('),('endChr',')')]:
                prop=node.find('./m:dPr/m:'+name,NS)
                value=default if prop is None else prop.get(f'{{{NS["m"]}}}val','')
                add(value,dict(loc,path=path+'/dPr/'+name,representation='math_delimiter'),True)
        if node.tag==f'{{{NS["m"]}}}groupChr':
            prop=node.find('./m:groupChrPr/m:chr',NS)
            value='⏟' if prop is None else prop.get(f'{{{NS["m"]}}}val','')
            add(value,dict(loc,path=path+'/groupChrPr/chr',representation='math_group_character'),True)
        # Other visible mathematical character properties can also carry fences.
        if node.tag in {f'{{{NS["m"]}}}chr',f'{{{NS["m"]}}}sepChr'}:
            add(node.get(f'{{{NS["m"]}}}val',''),dict(loc,representation='math_character'))
        if node.tag in {f'{{{NS["w"]}}}sym',f'{{{NS["w"]}}}fldChar',f'{{{NS["w"]}}}fldSimple',
                        f'{{{NS["w"]}}}numPr',f'{{{NS["a"]}}}blip',f'{{{NS["o"]}}}OLEObject'}:
            candidates.append(dict(loc,reason='generated_or_nonliteral_content_requires_existing_visual_review'))
        counts={}
        for child in node:
            tag=child.tag.split('}')[-1];counts[tag]=counts.get(tag,0)+1
            walk(child,f'{path}/{tag}[{counts[tag]}]',p,math,group)
    walk(root,root.tag.split('}')[-1])
    wording=[]
    for (p,path),chunks in texts.items():
        text=''.join(chunks)
        for match in PREFERRED_PATTERN.finditer(text):
            wording.append({'part':part,'paragraph':p,'path':path,'offset':match.start(),'text':match[0],
                            'replacement':PREFERRED_WORDING[match[0]],'context':text[max(0,match.start()-24):match.end()+32],
                            'offset_projection':'plain_text_with_OMML_boundary_placeholder'})
    return {'hits':hits,'formula_protected_candidates':formula_hits,'review_candidates':candidates,'preferred_wording_hits':wording}


def applied_numbering(paragraph, styles):
    """Resolve numId/ilvl without pretending to render Word list labels."""
    style_map=styles
    vals={};props=paragraph.find('./w:pPr',NS);seen=set()
    st=props.find('./w:pStyle',NS) if props is not None else None
    style_id=st.get(f'{{{NS["w"]}}}val') if st is not None else next((k for k,s in style_map.items()
        if s.get(f'{{{NS["w"]}}}type')=='paragraph' and s.get(f'{{{NS["w"]}}}default')=='1'),None)
    chain=[props]
    while style_id and style_id not in seen:
        seen.add(style_id);style=style_map.get(style_id)
        if style is None:break
        chain.append(style.find('./w:pPr',NS));base=style.find('./w:basedOn',NS)
        style_id=base.get(f'{{{NS["w"]}}}val') if base is not None else None
    for prop in chain:
        if prop is None:continue
        for key in ('numId','ilvl'):
            n=prop.find('./w:numPr/w:'+key,NS)
            if n is not None and key not in vals:vals[key]=n.get(f'{{{NS["w"]}}}val')
    return vals


def numbered_punctuation(paragraph, numbering, styles):
    """Resolve directly applied or paragraph-style inherited list label fences.

    Linked numbering styles and renderer-specific labels remain visual evidence;
    unused list definitions must never become automatic document violations.
    """
    if numbering is None:return []
    vals=applied_numbering(paragraph, styles)
    num_id=vals.get('numId');level=vals.get('ilvl','0')
    if not num_id or num_id=='0':return []
    num=next((n for n in numbering.findall('./w:num',NS) if n.get(f'{{{NS["w"]}}}numId')==num_id),None)
    if num is None:return []
    lvl=next((n.find('./w:lvl',NS) for n in num.findall('./w:lvlOverride',NS)
              if n.get(f'{{{NS["w"]}}}ilvl')==level and n.find('./w:lvl',NS) is not None),None)
    if lvl is None:
        aid=num.find('./w:abstractNumId',NS)
        abstract=next((n for n in numbering.findall('./w:abstractNum',NS)
                       if aid is not None and n.get(f'{{{NS["w"]}}}abstractNumId')==aid.get(f'{{{NS["w"]}}}val')),None)
        if abstract is not None:lvl=next((n for n in abstract.findall('./w:lvl',NS) if n.get(f'{{{NS["w"]}}}ilvl')==level),None)
    label=lvl.find('./w:lvlText',NS) if lvl is not None else None
    text=label.get(f'{{{NS["w"]}}}val','') if label is not None else ''
    return [{'path':f'numbering/num[{num_id}]/level[{level}]/lvlText','offset':i,'text':c,
             'codepoint':f'U+{ord(c):04X}','context':text,'representation':'applied_numbering'}
            for i,c in enumerate(text) if forbidden_punctuation(c)]


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
            'single_equation_continuation_candidate':single_line_math.continuation_rows(math) is not None,
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


FLOW_DEFAULTS = {'keepLines': False, 'keepNext': False, 'pageBreakBefore': False, 'widowControl': True}


def page_flow_reader(styles_root):
    """Resolve only pagination on/off properties, with their actual provenance.

    A missing style, a cycle or an invalid on/off value is unresolved rather
    than silently becoming a safe default. This does not simulate pagination.
    """
    w = '{'+NS['w']+'}'
    catalog = {s.get(w+'styleId'):s for s in styles_root.findall('./w:style',NS)}
    defaults = styles_root.find('./w:docDefaults/w:pPrDefault/w:pPr',NS)
    normal = [s.get(w+'styleId') for s in catalog.values()
              if s.get(w+'type')=='paragraph' and s.get(w+'default') in {'1','true','on'}]
    def read(paragraph):
        props=paragraph.find('./w:pPr',NS); style=props.find('./w:pStyle',NS) if props is not None else None
        sid=style.get(w+'val') if style is not None else (normal[0] if len(normal)==1 else None)
        chain=[('direct',props)]; seen=set(); unresolved=[]; heading=False
        if len(normal)>1 and style is None:unresolved.append('multiple_default_paragraph_styles')
        while sid:
            if sid in seen:unresolved.append('style_cycle:'+sid);break
            seen.add(sid);st=catalog.get(sid)
            if st is None:unresolved.append('missing_style:'+sid);break
            chain.append(('style:'+sid,st.find('./w:pPr',NS)))
            name=st.find('./w:name',NS);label=(name.get(w+'val','') if name is not None else sid)
            if re.search(r'^(?:heading\s*\d*|title|subtitle|标题\s*\d*|副标题)$',label,re.I):heading=True
            base=st.find('./w:basedOn',NS);sid=base.get(w+'val') if base is not None else None
        for _,pr in chain:
            outline=pr.find('./w:outlineLvl',NS) if pr is not None else None
            if outline is not None and outline.get(w+'val','9')!='9':heading=True
        result={};chain_unresolved=bool(unresolved)
        for key,default in FLOW_DEFAULTS.items():
            found=None
            for source,pr in chain:
                nodes=pr.findall('./w:'+key,NS) if pr is not None else []
                if len(nodes)>1:
                    unresolved.append('duplicate_property:'+source+':'+key)
                    found={'value':None,'source':source,'raw':None};break
                if nodes:
                    raw=nodes[0].get(w+'val','1')
                    value=True if raw in {'1','true','on'} else False if raw in {'0','false','off'} else None
                    if value is None:unresolved.append('invalid_on_off:'+source+':'+key)
                    found={'value':value,'source':source,'raw':raw};break
            if found is None and chain_unresolved:
                found={'value':None,'source':'unresolved_style_chain','raw':None}
            if found is None:
                node=defaults.find('./w:'+key,NS) if defaults is not None else None
                raw=node.get(w+'val','1') if node is not None else None
                value=(True if raw in {'1','true','on'} else False if raw in {'0','false','off'} else None) if node is not None else default
                if node is not None and value is None:unresolved.append('invalid_on_off:docDefaults:'+key)
                found={'value':value,'source':'docDefaults' if node is not None else 'ooxml_default','raw':raw}
            result[key]=found
        return {'effective':result,'unresolved':sorted(set(unresolved)),'heading_style':heading}
    return read


def paragraph_content_evidence(paragraph):
    """Distinguish empty carriers from formulas, images, OLE, fields and glyphs.

    No deletion decision is returned. Textless content is never treated as an
    empty paragraph merely because it has no w:t descendant.
    """
    w='{'+NS['w']+'}';m='{'+NS['m']+'}'
    objects=set();chunks=[];graphic_text=[];manual_breaks=0;rendered_breaks=0
    def walk(node,math=False,graphic=False):
        nonlocal manual_breaks,rendered_breaks
        if node is not paragraph and node.tag==w+'p':return
        if node.tag in {w+'del',w+'moveFrom'}:return
        if node.tag in {m+'oMath',m+'oMathPara'}:objects.add('omml');math=True
        if node.tag in {w+'drawing',w+'pict','{'+NS['a']+'}blip'} | {'{'+NS['v']+'}'+n for n in ('imagedata','shape','group','line')}:
            objects.add('image_or_shape');graphic=True
        if node.tag in {w+'object','{'+NS['o']+'}OLEObject'}:objects.add('ole');graphic=True
        if node.tag in {w+'fldChar',w+'fldSimple',w+'instrText',w+'footnoteReference',w+'endnoteReference'}:objects.add('field')
        if node.tag==w+'sym':objects.add('symbol')
        if node.tag==w+'br':
            objects.add('break_or_tab')
            if node.get(w+'type')=='page':manual_breaks+=1
        if node.tag in {w+'tab',w+'cr'}:objects.add('break_or_tab')
        if node.tag==w+'lastRenderedPageBreak':rendered_breaks+=1
        if node.tag==w+'t' and not math and not graphic:chunks.append(node.text or '')
        if graphic and not math and node.tag in {w+'t','{'+NS['a']+'}t'} and node.text:
            graphic_text.append(node.text)
        for child in node:walk(child,math,graphic)
    walk(paragraph)
    plain=''.join(chunks);boundary=paragraph.find('./w:pPr/w:sectPr',NS) is not None
    kind='text' if plain.strip() else 'object' if objects else 'empty'
    if not plain.strip(' \t\r\n；;。.') and objects=={'omml'}:kind='display_formula'
    return {'kind':kind,'plain_text':plain,'graphic_text':graphic_text,'objects':sorted(objects),'section_boundary':boundary,
            'manual_page_breaks':manual_breaks,'last_rendered_page_breaks':rendered_breaks}


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
    """Only a bare ASCII letter; never flatten Greek, scripts or operators."""
    children = [n for n in math if n.tag != f'{{{NS["m"]}}}oMathPr']
    if len(children) != 1 or children[0].tag != f'{{{NS["m"]}}}r':
        return False
    run = children[0]
    allowed = {f'{{{NS["m"]}}}{n}' for n in ['t', 'rPr']} | {f'{{{NS["w"]}}}rPr'}
    if any(n.tag not in allowed for n in run):
        return False
    text = visible_text(math)
    return bool(re.fullmatch(r'[A-Za-z]', text))


def math_operator_names(math):
    """Names evidenced by actual function application, never a label list."""
    m='{'+NS['m']+'}'
    names=set()
    for parent in math.iter():
        children=list(parent)
        for left,right in zip(children,children[1:]):
            if left.tag==m+'r' and right.tag==m+'d':
                name=visible_text(left)
                if re.fullmatch(r'[A-Za-zΑ-ωϐ-Ͽ]+',name):names.add(name)
        if parent.tag==m+'func':
            name=parent.find('m:fName',NS);argument=parent.find('m:e',NS)
            if name is not None and argument is not None:
                value=visible_text(name)
                if re.fullmatch(r'[A-Za-zΑ-ωϐ-Ͽ]+',value):names.add(value)
    return names


def inline_formula_candidates(tokens, part, number, operators=None):
    """Locate bounded mathematical spans; identity remains a source review.

    Tokens retain their original Word/math ownership, so a relation split
    across runs or across an OMML boundary is not missed. This deliberately
    does not classify arbitrary ASCII labels or all strings containing '='.
    """
    text=''.join(value for kind,value in tokens)
    kinds=[kind for kind,value in tokens for _ in value]
    owners=[i if kind=='math' else None for i,(kind,value) in enumerate(tokens) for _ in value]
    math_letter=r'A-Za-zΑ-ωϐ-Ͽ\u2100-\u214f\U0001D400-\U0001D7CB'
    # Keep text-rendered accents/scripts inside the located equation. Numeric
    # superscripts alone do not establish a symbolic mathematical identity.
    chars=math_letter+r'0-9_=＝+−±∓\-*/×÷^().,{}\[\]·∆Δ≤≥<>≈≠≔∑∏∫√∞∂∇|% \t\u00a0\u0300-\u036f\u20d0-\u20ff\u2032-\u2037\u2070-\u209f\u1d2c-\u1d6a\u00bc-\u00be\u2150-\u215f⁄²³¹'
    spans=re.finditer('['+chars+']+',text)
    rows=[]
    numeric=re.compile(r'(?<![A-Za-z_])[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?')
    names={'log','exp','ln','sin','cos','tan','sg','max','min','argmax','argmin','lim'} | set(operators or {})
    def symbolic(value):
        # Scientific exponents are numbers, not an occurrence of variable e.
        rest=numeric.sub('',value)
        words=re.findall(r'[A-Za-z]+',rest)
        if any(len(word)>1 and word not in names for word in words):return False
        return bool(words or re.search(r'[Α-ωϐ-Ͽ∆Δ\u2100-\u214f\U0001D400-\U0001D7CB∑∏∫√∞∂∇]',rest))
    def relation(value):
        sides=re.split('[=＝]',value)
        return len(sides)>1 and all(side.strip() for side in sides)
    def clauses(value):
        # Split separate equalities, retaining commas in function arguments
        # and in a complete indexed quantity on either side of the equality.
        start=0;depth=0
        for i,char in enumerate(value):
            if char in '([{':depth+=1
            elif char in ')]}':depth=max(0,depth-1)
            elif char==',' and depth==0 and relation(value[start:i]) and relation(value[i+1:]):
                yield start,i
                start=i+1
        yield start,len(value)
    for match in spans:
        for a,z in clauses(match[0]):
            raw=match[0][a:z];value=raw.strip(', \t\u00a0')
            if not relation(value):continue
            start=match.start()+a+len(raw)-len(raw.lstrip(', \t\u00a0'))
            end=start+len(value)
            owned=kinds[start:end];objects=set(owners[start:end])
            # OMML scripts flatten to e.g. xi,j in this locator projection.
            # Use original object ownership as candidate evidence instead of
            # treating that flattened spelling as an arbitrary ASCII label.
            math_symbol=any(re.search('['+math_letter+r'∆Δ∑∏∫√∞∂∇]',numeric.sub('',
                ''.join(text[i] for i in range(start,end) if owners[i]==owner)))
                for owner in objects-{None})
            if not symbolic(value) and not math_symbol:continue
            if None not in objects:
                if len(objects)==1:continue
                # Adjacent independently complete math objects need no merge.
                # A split x / = / 4 is still one equation and is a candidate.
                pieces=[''.join(text[i] for i in range(start,end) if owners[i]==owner)
                        for owner in sorted(objects)]
                if all(relation(piece) for piece in pieces):continue
            rows.append({'part':part,'paragraph':number,'path':f'p[{number}]',
                         'offset':start,'end_offset':end,'text':value,
                         'offset_projection':'visible_text_with_OMML_linear_text',
                         'reason':'inline_symbolic_relation_requires_complete_source_math',
                         'representation':('mixed' if len(set(owned))>1 else owned[0]),
                         'math_object_count':len(objects-{None}),
                         'review_boundary':'Classify this exact span against source. A symbolic equation including a numeric assignment or numeric-argument function must be one complete math object. Actual format errors cannot be waived by source identity.'})
    for name,locations in (operators or {}).items():
        # Explicit definition wording only; a technical term merely containing
        # these letters or an operator with its argument/placeholder is not hit.
        pattern=r'(?<![A-Za-zΑ-ωϐ-Ͽ])'+re.escape(name)+r'(?=表示|代表)'
        for match in re.finditer(pattern,text):
            rows.append({'part':part,'paragraph':number,'path':f'p[{number}]',
                         'offset':match.start(),'end_offset':match.end(),'text':name,
                         'offset_projection':'visible_text_with_OMML_linear_text',
                         'reason':'source_operator_explanation_requires_complete_argument_notation',
                         'operator_calls':list(locations),
                         'review_boundary':'Only actual source-bound operator identity can resolve this candidate. Repair a missing argument placeholder; do not infer operators from labels.'})
    return rows


CJK_MATH = re.compile(r'[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]+')


def math_notation_candidates(math_node):
    """Locate visible notation only; never infer transpose or translate a label."""
    out=[]
    prefixes={NS['m']:'m',NS['w']:'w'}
    def walk(node,path,parent_tag=None):
        if node.tag in {'{'+NS['m']+'}t','{'+NS['w']+'}t'}:
            for match in CJK_MATH.finditer(node.text or ''):
                out.append(dict(reason='math_cjk_requires_english_symbol',math_path=path,
                                node_offset=match.start(),node_end_offset=match.end(),text=match[0]))
        if (node.tag=='{'+NS['m']+'}sup' and
                parent_tag in {'{'+NS['m']+'}sSup','{'+NS['m']+'}sSubSup'} and
                visible_text(node,True)=='T'):
            out.append(dict(reason='superscript_T_requires_role_review',math_path=path,text='T'))
        counts={}
        for child in node:
            counts[child.tag]=counts.get(child.tag,0)+1
            uri,local=child.tag[1:].split('}',1) if child.tag.startswith('{') else ('',child.tag)
            name=(prefixes[uri]+':' if uri in prefixes else '')+local
            walk(child,path+'/'+name+'['+str(counts[child.tag])+']',node.tag)
    walk(math_node,'.')
    return out


def formula_prose_paragraph_evidence(paragraph, part, number, object_ids, previous_text='', operators=None):
    """Locate v23 roles during the existing inspection, without rewriting math.

    A mathematical-looking string is not a technical formula identity. Only an
    unambiguous standalone math carrier is checked for terminal punctuation;
    labels, multiple objects and nonliteral carriers remain semantic review.
    """
    out={k:[] for k in ('explanation_lead_hits','standalone_formula_semicolon_hits',
        'bare_symbol_omml_candidates','plain_label_omml_candidates','inline_numeric_omml_candidates',
        'review_candidates','standalone_formulas','group_separator_hits','single_equation_continuation_hits')}
    w='{'+NS['w']+'}';m='{'+NS['m']+'}'
    tokens=[];maths=[];nonliteral=[]
    def walk(node,path):
        if node.tag in {w+'del',w+'moveFrom'}:return
        if node is not paragraph and node.tag==w+'p':return
        if node.tag==m+'oMath':
            row={'part':part,'paragraph':number,'path':path,'object_id':object_ids[id(node)],
                 'text':visible_text(node,True),'representation':'omml'}
            maths.append((node,row));tokens.append(('math',row['text']));return
        if node.tag==w+'t':tokens.append(('text',node.text or ''))
        if node.tag in {w+n for n in ('sym','fldChar','fldSimple','instrText','drawing','pict','object','br','tab','cr')}:
            nonliteral.append(path)
        counts={}
        for child in node:
            tag=child.tag.split('}')[-1];counts[tag]=counts.get(tag,0)+1
            walk(child,f'{path}/{tag}[{counts[tag]}]')
    base=f'p[{number}]';walk(paragraph,base)
    plain=''.join(text for kind,text in tokens if kind=='text')
    projection=''.join(text if kind=='text' else '\ufffc' for kind,text in tokens)
    lead=re.match(r'^\s*(式中[，,：:])',projection)
    if lead:
        out['explanation_lead_hits'].append({'part':part,'paragraph':number,'path':base,
            'offset':lead.start(1),'text':lead[1],'replacement':'其中'+lead[1][-1],
            'context':projection[:100],'offset_projection':'plain_text_with_OMML_boundary_placeholder'})
    props=paragraph.find('./w:pPr',NS)
    jc=props.find('./w:jc',NS) if props is not None else None
    centered=jc is not None and jc.get(w+'val')=='center'
    display=paragraph.find('.//m:oMathPara',NS) is not None
    out['review_candidates'].extend(inline_formula_candidates(tokens,part,number,operators))
    # Only ordinary bare ASCII letters can use plain prose. Greek, other
    # Unicode symbols and real OMML structures keep their math representation.
    def bare_runs(node):
        children=[n for n in node if n.tag!=m+'oMathPr']
        return bool(children) and all(n.tag==m+'r' and all(c.tag in {m+'t',m+'rPr',w+'rPr'} for c in n)
                                      for n in children)
    for node,row in maths:
        out['review_candidates'].extend(dict(row,**hit) for hit in math_notation_candidates(node))
        text=row['text']
        if bare_runs(node) and re.fullmatch(r'[A-Za-z]',text):
            out['bare_symbol_omml_candidates'].append(dict(row,reason='bare_prose_symbol_requires_plain_run_or_source_math_identity'))
        elif bare_runs(node) and re.fullmatch(r'[A-Za-z]{2,}',text):
            out['plain_label_omml_candidates'].append(dict(row,reason='flat_ascii_label_requires_plain_run_or_source_math_identity'))
        elif bare_runs(node) and plain.strip() and re.fullmatch(
                r'[+−-]?\d+(?:\.\d+)?(?:[+−\-×÷*/]\d+(?:\.\d+)?)+=[+−-]?\d+(?:\.\d+)?',
                re.sub(r'\s+','',text)):
            out['inline_numeric_omml_candidates'].append(dict(row,reason='inline_numeric_calculation_requires_plain_prose_or_source_math_identity'))
    if not maths:return out
    carrier_plain=not plain.strip(' \t\r\n；;。.') and not nonliteral
    if len(maths)!=1:
        if (centered or display) and carrier_plain:
            out['review_candidates'].append({'part':part,'paragraph':number,'path':base,
                'object_ids':[row['object_id'] for _,row in maths],
                'reason':'multiple_math_objects_require_semantic_block_and_punctuation_review'})
        return out
    node,row=maths[0]
    if not carrier_plain:
        # A formula number or nonliteral object can follow a display equation.
        # Never mistake that mixed carrier for an objectively punctuable block.
        if centered or display:
            out['review_candidates'].append(dict(row,reason='display_label_prose_or_nonliteral_carrier_requires_semantic_review'))
        return out
    text=row['text'].strip()
    structures={n.tag for n in node.iter()}
    compound=bool(structures & {m+n for n in ('f','nary','limLow','limUpp','eqArr','m')})
    introduction=bool(re.search(r'下式|以下(?:表达|公式)|计算式|公式(?:为|如下)|表示为|表达式为',previous_text))
    statement=bool(re.search(r'[=≠≤≥≈≔<>]',text) and re.search(r'[^=≠≤≥≈≔<>\s]',text))
    def terminal_glyph(math_node):
        # visible_text omits generated fences and script layout. A semicolon
        # inside a closing delimiter or a subscript is not block punctuation.
        if math_node.tag in {m+'r',m+'t',w+'t'}:return visible_text(math_node).rstrip()[-1:]
        if math_node.tag==m+'d':
            end=math_node.find('./m:dPr/m:endChr',NS)
            glyph=')' if end is None else end.get(m+'val','')
            if glyph:return glyph[-1:]
        elif math_node.tag==m+'eqArr':
            rows=math_node.findall('m:e',NS)
            return terminal_glyph(rows[-1]) if rows else ''
        elif math_node.tag not in {m+'oMath',m+'e'}:
            return '\ufffc'
        for child in reversed(list(math_node)):
            if child.tag.startswith(m) and child.tag.split('}')[-1].endswith('Pr'):continue
            glyph=terminal_glyph(child)
            if glyph:return glyph
        return ''
    # Only a direct array of independently complete equality rows is a known
    # formula group. Continuations, cases/conditions and matrices stay under
    # the existing semantic review instead of guessed comma insertion.
    children=[n for n in node if n.tag!=m+'oMathPr']
    if (centered or display) and single_line_math.continuation_rows(node) is not None:
        out['single_equation_continuation_hits'].append(dict(row,reason='single_equation_display_continuation_requires_single_line'))
    if len(children)==1 and children[0].tag==m+'eqArr':
        array=children[0];entries=array.findall('m:e',NS)
        complete_rows=[]
        for entry in entries:
            direct=''.join(visible_text(child) if child.tag==m+'r' else '\ufffc' for child in entry)
            core=direct.rstrip(',，;；')
            relation=re.fullmatch(r'([^=<>≤≥≠]+)=([^=<>≤≥≠]+)',core)
            complex_case=(any(n.tag==m+'m' for n in entry.iter()) or
                          any(n.get(m+'val') in {'{','｛'} for n in entry.findall('.//m:begChr',NS)) or
                          bool(re.search(r'[\u3400-\u9fff]|\b(?:if|when|otherwise)\b',direct)))
            complete_rows.append(bool(relation and not complex_case))
        if len(entries)>1 and all(complete_rows):
            for index,entry in enumerate(entries,1):
                # Count only the row's outer trailing punctuation, including
                # adjacent runs. A comma inside a final function delimiter is
                # part of that function, not an equation-row separator.
                ending=''
                for child in reversed(list(entry)):
                    if child.tag.startswith(m) and child.tag.split('}')[-1].endswith('Pr'):continue
                    if child.tag!=m+'r':break
                    value=visible_text(child)
                    if not value:continue
                    tail=re.search(r'[,，;；]+$',value)
                    if tail is None:break
                    ending=tail[0]+ending
                    if tail.start():break
                if len(ending)>1:
                    out['group_separator_hits'].append(dict(row,
                        path=row['path']+f'/eqArr[1]/e[{index}]',row=index,
                        reason='complete_equation_group_row_has_duplicate_terminal_punctuation',
                        terminal=ending))
                elif index<len(entries) and terminal_glyph(entry) not in {',','，'}:
                    out['group_separator_hits'].append(dict(row,
                        path=row['path']+f'/eqArr[1]/e[{index}]',row=index,
                        reason='complete_equation_group_nonfinal_row_requires_comma',
                        terminal=terminal_glyph(entry)))
    # Layout + accepted carrier + substantive expression/context are all
    # required. Equality or object count by itself never establishes a role.
    if (centered or display) and (compound or (statement and (introduction or re.search(r'[A-Za-zΑ-ω]',text)))):
        last=next(((kind,value.rstrip()) for kind,value in reversed(tokens) if value.strip()),('',''))
        terminal=(last[1][-1:] if last[0]=='text' else terminal_glyph(node))
        block=dict(row,terminal_semicolon=terminal in {'；',';'},
                   role_basis={'centered':centered,'display_container':display,
                               'compound_structure':compound,'formula_introduction':introduction,
                               'statement_shape':statement})
        out['standalone_formulas'].append(block)
        if not block['terminal_semicolon']:
            out['standalone_formula_semicolon_hits'].append(dict(block,reason='standalone_formula_missing_terminal_semicolon'))
    elif centered or display:
        out['review_candidates'].append(dict(row,reason='standalone_math_identity_requires_semantic_review'))
    return out


def wording_evidence(body, claims):
    """Locate edits by scope; never replace titles or certify antecedents."""
    rows = []
    claim_by_p = {p: c['number'] for c in claims for p in c['paragraphs']}
    for p in body:
        text = p['text']
        claim = claim_by_p.get(p['p'])
        for m in re.finditer('从而|一一', text):
            rows.append({'paragraph': p['p'], 'offset': m.start(), 'text': m[0],
                         'context': text[max(0,m.start()-24):m.end()+32], 'claim': claim,
                         'category': 'global_causal_connector' if m[0]=='从而' else 'global_yiyi_modifier_candidate',
                         'replacement': None, 'status': 'rephrase_preserving_causality_and_cardinality_not_blind_deletion'})
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
    contract = document_contract_evidence(body)
    rows.extend(dict(hit, claim=None, category='description_claim_word', replacement=None,
                     status='rewrite_as_description_preserving_source') for hit in contract['description_word_hits'])
    return {'occurrences': rows,
            'boundary': 'Claims-only neighbor/comparative qualifier/open enumeration rules do not apply elsewhere. Module terms require protected names/titles to remain unchanged. This is read-only candidate evidence; 等于/相等/等级 and 比较 are not automatically deleted. Check images/fields not represented as ordinary text separately.'}


def document_contract_evidence(body):
    """Read-only title and scoped wording evidence; no semantic acceptance."""
    direct = [p for p in body if p.get('body_direct', True)]
    fields = [n for n,p in enumerate(direct) if p['text'].strip() == '技术领域']
    title = None
    if len(fields) == 1:
        prior = next((p for p in reversed(direct[:fields[0]]) if p['text'].strip()), None)
        if prior and prior['text'].strip() not in {'说明书','权利要求书','说明书摘要','摘要附图','说明书附图'}:
            title = {'text': prior['text'].strip(), 'paragraph': prior['p'],
                     'evidence': 'nonempty_direct_paragraph_before_technical_field'}
    start = title['paragraph'] if title else (direct[fields[0]]['p'] if len(fields)==1 else None)
    end = next((p['p'] for p in body if start is not None and p['p']>start and
                (p.get('default_header','').replace(' ','') == '说明书附图' or p['text'].strip() == '说明书附图')), None)
    description = [p for p in body if (start is not None and p['p'] >= start and (end is None or p['p'] < end)) or
                   re.sub(r'\s+', '', p.get('default_header','')) == '说明书']
    hits = lambda paragraphs,pattern: [
        {'part':'word/document.xml','paragraph':p['p'],'path':f'paragraph[{p["p"]}]/visible_text',
         'offset':m.start(),'text':m[0], 'context':p['text'][max(0,m.start()-24):m.end()+32],
         'formula_protected_candidate':any(s['start']<=m.start() and s['end']>=m.end() for s in p.get('formula_text_spans',[]))}
        for p in paragraphs for m in re.finditer(pattern,p['text'])]
    return {'schema_version':1, 'title':title, 'description_scope_resolved':bool(description),
            'global_word_hits':hits(body,'从而|一一'),
            'description_word_hits':hits(description,'权利要求|特征在于'),
            'status':'objective_candidates_not_semantic_pass'}


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


def paragraph_containers(root):
    """One traversal records graphic ancestry without treating it as visual proof."""
    result={}
    w='{'+NS['w']+'}'
    def walk(node,graphic=False,deleted=False):
        graphic=graphic or node.tag in {w+'drawing',w+'pict',w+'object'}
        deleted=deleted or node.tag in {w+'del',w+'moveFrom'}
        if node.tag==w+'p':result[id(node)]={'in_graphic':graphic,'deleted':deleted}
        for child in node:walk(child,graphic,deleted)
    walk(root)
    return result


def formula_text_spans(paragraph):
    """Map native formula ranges into the existing accepted visible-text view."""
    spans=[];position=0
    def walk(node):
        nonlocal position
        if node.tag in {f'{{{NS["w"]}}}del',f'{{{NS["w"]}}}moveFrom'}:return
        if node.tag==f'{{{NS["m"]}}}oMath':
            start=position;position+=len(visible_text(node,True));spans.append({'start':start,'end':position});return
        if node.tag in {f'{{{NS["w"]}}}{n}' for n in ('t','tab','br','sym')} | {f'{{{NS["m"]}}}t'}:
            position+=len(visible_text(node,True));return
        for child in node:
            if child.tag!=f'{{{NS["w"]}}}p':walk(child)
    walk(paragraph);return spans


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
    start = 0; evidence=[]
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
            paragraph['default_header'] = headers.get('default', '')
            paragraph['section_index'] = len(evidence)+1
        evidence.append({'section':len(evidence)+1,'start_paragraph':start+1,'end_paragraph':end,
                         'default_header':headers.get('default',''),
                         'properties':{n.tag.split('}')[-1]:{k.split('}')[-1]:v for k,v in n.attrib.items()}
                                       for n in section if n.tag.split('}')[-1] in {'type','pgSz','pgMar','docGrid'}}})
        start = end
    return evidence


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
              'content_spacing_evidence': {'schema_version':1,'artifact_sha256':None,
                  'complete':False,'extraction_complete':False,'hits':[],
                  'scope':'Literal Unicode whitespace plus U+200B/U+FEFF in current w:t/m:t/a:t, chart cache text, VML text and actual w:tab content across body, tables, text boxes, headers/footers and extra drawing parts. Excludes deleted/moved-from revisions, field instructions, properties/tab-stop definitions, XML indentation, character tracking and natural math spacing. Offsets are per located text node; no automatic stripping or word concatenation. Images, encoded glyphs and generated labels retain the existing source and final-page review.'},
              'formula_prose_evidence': {'schema_version':1,'artifact_sha256':None,
                  'extraction_complete':False,'complete':False,'explanation_lead_hits':[],
                  'standalone_formula_semicolon_hits':[],'bare_symbol_omml_candidates':[],'plain_label_omml_candidates':[],
                  'inline_numeric_omml_candidates':[],'review_candidates':[],'standalone_formulas':[],
                  'group_separator_hits':[],'single_equation_continuation_hits':[],
                  'duplicate_formula_candidates':[],
                  'scope':'Located formatting candidates only. Formula identity is source-semantic; complex OMML, text/image/OLE formulas, labels and mixed carriers retain the existing independent semantic and final-page review.'},
              'punctuation_evidence': {'schema_version':2,'literal_scan_complete':False,'hits':[],
                  'formula_protected_candidates':[],'preferred_wording_hits':[],'review_candidates':[],
                  'scope':'Prose characters are candidates; OMML punctuation is protected pending original source/formula mapping review. Text/image/OLE formulas, numbering, fields, glyphs and angle characters require existing semantic and final-page review.'},
              'text_color_evidence': {'review_runs': [],
                  'scope': 'Applied document-default, paragraph/character basedOn and direct Word/OMML run font colors. Unused styles ignored. Automatic colors, table conditional formatting, DrawingML/image text, highlighting and revision display still require final visual review.'}}
    result['formula_prose_evidence']['artifact_sha256']=result['sha256']
    result['content_spacing_evidence']['artifact_sha256']=result['sha256']
    equation_occurrences = {}
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
            raise ValueError('Uncompressed DOCX exceeds 512 MiB inspection limit')
        names = z.namelist()
        if 'word/document.xml' not in names:
            raise ValueError('Missing word/document.xml')
        if z.testzip() is not None:
            raise ValueError('DOCX ZIP CRC failure')
        read_colors = color_reader(z)
        numbering_root=ET.fromstring(z.read('word/numbering.xml')) if 'word/numbering.xml' in names else None
        applied_styles={};styles_root=ET.Element('styles')
        # Keep compact style definitions so paragraph/header inheritance can be
        # resolved without reopening the ZIP. This is not an effective-style verdict.
        if 'word/styles.xml' in names:
            styles_root = ET.fromstring(z.read('word/styles.xml'))
            applied_styles={s.get(f'{{{NS["w"]}}}styleId'):s for s in styles_root.findall('./w:style',NS)}
            default = styles_root.find('./w:docDefaults', NS)
            result['style_evidence'] = {
                'doc_defaults_xml': ET.tostring(default, encoding='unicode') if default is not None else None,
                'styles': [{'id': s.get(f'{{{NS["w"]}}}styleId'),
                            'type': s.get(f'{{{NS["w"]}}}type'),
                            'default': s.get(f'{{{NS["w"]}}}default'),
                            'xml': ET.tostring(s, encoding='unicode')}
                           for s in styles_root.findall('./w:style', NS)],
                'boundary': 'Definitions, not applied formatting. Resolve actual styles, inheritance, direct overrides and final renderer output.'}
        read_page_flow=page_flow_reader(styles_root)
        parts = [n for n in names if re.fullmatch(r'word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n)]
        parts.sort(key=lambda n: (n != 'word/document.xml', n))
        for part in parts:
            root = ET.fromstring(z.read(part))
            result['content_spacing_evidence']['hits'].extend(content_spacing_hits(root, part))
            punctuation=punctuation_evidence(root,part)
            for key in punctuation:result['punctuation_evidence'][key].extend(punctuation[key])
            rels = relationships(z, part)
            paragraphs = []
            operators = {}
            direct_paragraphs = {id(p) for p in root.findall('./w:body/w:p', NS)} if part == 'word/document.xml' else set()
            containers = paragraph_containers(root)
            for index, p in enumerate(root.findall('.//w:p', NS), 1):
                result['punctuation_evidence']['hits'].extend(dict(h,part=part,paragraph=index)
                    for h in numbered_punctuation(p,numbering_root,applied_styles))
                owned_nodes = list(paragraph_owned_nodes(p))
                removed_math = {id(math) for node in owned_nodes
                                if node.tag in {f'{{{NS["w"]}}}del', f'{{{NS["w"]}}}moveFrom'}
                                for math in node.iter(f'{{{NS["m"]}}}oMath')}
                result['text_color_evidence']['review_runs'].extend(read_colors(p, owned_nodes, part, index))
                formula_ids = []
                object_ids = {}
                for math_index, math in enumerate((n for n in owned_nodes if n.tag == f'{{{NS["m"]}}}oMath'), 1):
                    fid = f'F{len(result["formulas"])+1:04}'
                    formula_ids.append(fid)
                    object_ids[id(math)] = fid
                    result['formulas'].append({'id':fid,'part':part,'paragraph':index,
                        'linear_text':visible_text(math),'omml_xml':ET.tostring(math,encoding='unicode'),
                        'math_evidence':math_evidence(math),
                        'isolated_symbol_candidate':isolated_symbol_candidate(math),
                        'status':'unverified'})
                    fingerprint = (None if id(math) in removed_math or containers[id(p)]['deleted']
                                   else formula_uniqueness.equation_fingerprint(math))
                    if fingerprint:
                        equation_occurrences.setdefault(fingerprint, []).append({
                            'part': part, 'paragraph': index, 'object_id': fid,
                            'path': f'p[{index}]/oMath[{math_index}]'})
                    for name in sorted(math_operator_names(math)):
                        calls=operators.setdefault(name,[])
                        calls.append({'part':part,'paragraph':index,'object_id':fid})
                prior=next((row['text'] for row in reversed(paragraphs) if row['text'].strip()),'')
                formula_prose=formula_prose_paragraph_evidence(p,part,index,object_ids,prior,operators)
                for key,rows in formula_prose.items():result['formula_prose_evidence'][key].extend(rows)
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
                    'formula_text_spans':formula_text_spans(p),
                    'body_direct':id(p) in direct_paragraphs,
                    'numbering_applied':applied_numbering(p,applied_styles),
                    'font_encoded_symbols':symbols,
                    'word_run_formats':word_run_formats(p),
                    'layout_evidence':paragraph_layout_evidence(p),
                    'page_flow':read_page_flow(p),
                    'container_evidence':containers[id(p)],
                    'content_evidence':paragraph_content_evidence(p)})
            section_evidence=[]
            if part == 'word/document.xml':
                section_evidence=attach_section_headers(root, paragraphs, rels, z)
            revisions = {kind:len(root.findall('.//w:'+kind, NS)) for kind in ['ins','del','moveFrom','moveTo']}
            result['parts'].append({'part':part,'paragraphs':paragraphs,'relationships':rels,'revisions':revisions,
                'section_properties':[ET.tostring(s,encoding='unicode') for s in root.findall('.//w:sectPr',NS)],
                'page_flow_sections':section_evidence,
                'body_objects':[{'path':f'body/{n.tag.split("}")[-1]}[{i}]','element':n.tag.split('}')[-1]}
                                for i,n in enumerate(root.findall('./w:body/*',NS),1)
                                if n.tag not in {f'{{{NS["w"]}}}p',f'{{{NS["w"]}}}sectPr'}] if part=='word/document.xml' else []})
        # DrawingML/chart text can live outside Word paragraph parts. Do not
        # inspect relationships, field instructions or XML syntax as prose.
        extra_parts=[n for n in names if n not in parts and
                     (re.fullmatch(r'word/(charts|diagrams)/.+\.xml',n) or
                      re.fullmatch(r'word/.+\.vml',n))]
        for name in extra_parts:
            root=ET.fromstring(z.read(name))
            punctuation=punctuation_evidence(root,name)
            for key in punctuation:result['punctuation_evidence'][key].extend(punctuation[key])
            result['content_spacing_evidence']['hits'].extend(content_spacing_hits(root,name))
        if 'word/numbering.xml' in names:
            for index,level in enumerate(numbering_root.findall('.//w:lvlText',NS),1):
                text=level.get(f'{{{NS["w"]}}}val','')
                if any(forbidden_punctuation(ch) for ch in text):
                    result['punctuation_evidence']['review_candidates'].append({'part':'word/numbering.xml',
                        'path':f'lvlText[{index}]','text':text,'reason':'numbering_definition_requires_applied_display_review_not_all_definitions_are_used'})
        result['punctuation_evidence']['literal_scan_complete']=True
        result['content_spacing_evidence']['extraction_complete']=True
        result['content_spacing_evidence']['complete']=True
        result['formula_prose_evidence']['duplicate_formula_candidates'] = [
            {'artifact_sha256': result['sha256'], 'fingerprint': fingerprint,
             'occurrences': occurrences,
             'reason': 'repeated_complete_equation_requires_source_identity_review'}
            for fingerprint, occurrences in equation_occurrences.items() if len(occurrences) > 1]
        result['formula_prose_evidence']['extraction_complete']=True
        result['formula_prose_evidence']['complete']=True
        for name in names:
            if name.startswith('word/media/') and not name.endswith('/'):
                data = z.read(name)
                result['media'].append({'part':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
            elif name.startswith('word/embeddings/') and not name.endswith('/'):
                result['embeddings'].append({'part':name,'bytes':z.getinfo(name).file_size,'status':'requires_object_review'})
    return result


def benefit_introduction(text):
    value=re.sub(r'\s+', '', text)
    return bool(re.fullmatch(r'(?:与现有技术相比[，,])?本发明的有益效果(?:在于|如下)?[：:]?|有益效果[：:]?',value))


def benefit_numbering_evidence(body):
    """Locate the invention's effect paragraphs; never rewrite text or math."""
    result={'schema_version':1,'scope_resolved':False,'items':[], 'formula_paragraphs':[], 'issues':[],
            'boundary':'Literal paragraph ordinals only; claim-family support and final display still need independent review.'}
    direct=[p for p in body if p.get('body_direct')]
    headings=[p for p in direct if p['text'].strip()=='发明内容']
    if len(headings)!=1:
        result['issues'].append({'code':'invention_heading_unresolved'});return result
    start=headings[0]['p']
    ends=[p['p'] for p in direct if p['p']>start and p['text'].strip() in {'附图说明','具体实施方式'}]
    if not ends:
        result['issues'].append({'code':'invention_end_unresolved'});return result
    end=min(ends)
    leads=[p for p in direct if start<p['p']<end and benefit_introduction(p['text'])]
    if len(leads)!=1:
        result['issues'].append({'code':'benefit_introduction_unresolved'});return result
    lead=leads[0]['p'];result.update(scope_resolved=True,introduction_paragraph=lead,end_paragraph=end)
    for p in body:
        if not lead<p['p']<end:continue
        text=p['text'].strip()
        if not text and not p.get('formulas') and not p.get('media'):continue
        locator={'part':'word/document.xml','paragraph':p['p'],
                 'path':f'paragraph[{p["p"]}]/visible_text','offset':0,'text':text}
        if not p.get('body_direct') or p.get('media'):
            result['issues'].append(dict(locator,code='effect_container_requires_rewrite_or_review'));continue
        # A source formula on its own remains attached to the preceding effect;
        # never turn its operands, indices or source equation number into a list number.
        if p.get('formulas') and (p.get('layout_evidence') or {}).get('math_only_text_candidate'):
            result['formula_paragraphs'].append(locator)
            if not result['items']:result['issues'].append(dict(locator,code='formula_without_effect'))
            continue
        match=re.fullmatch(r'([1-9][0-9]*)\.([^\s].*)',text,re.S)
        number=int(match[1]) if match else None
        row=dict(locator,number=number,text=text,expected_number=len(result['items'])+1)
        result['items'].append(row)
        if not match:result['issues'].append(dict(locator,code='missing_or_noncanonical_effect_number'))
        elif number!=row['expected_number']:
            result['issues'].append(dict(locator,code='nonconsecutive_effect_number'))
        if (p.get('numbering_applied') or {}).get('numId') not in (None,'0'):
            result['issues'].append(dict(locator,code='native_list_label_requires_literal_conversion'))
    if not result['items']:result['issues'].append({'code':'empty_benefit_group'})
    return result


def page_flow_evidence(result, body, claims):
    """Located structural candidates only; never infer the filled area of a page."""
    part=next(p for p in result['parts'] if p['part']=='word/document.xml')
    out={'schema_version':1,'artifact_sha256':result['sha256'],'extraction_complete':True,
         'paragraphs':[],'ordinary_violations':[],'abstract_labels':[],'final_captions':[],
         'empty_candidates':[],'review_candidates':[],'sections':part.get('page_flow_sections',[]),
         'boundary':'Effective pagination and located content only. No page-fill, clipping or visual-acceptance verdict; existing six reviews and final rendered pages remain required.'}
    roles=[('说明书摘要','abstract'),('摘要附图','abstract_figure'),('权利要求书','claims'),
           ('说明书附图','figures'),('说明书','description')]
    def role(text):
        clean=re.sub(r'\s+','',text)
        return next((value for label,value in roles if clean.startswith(label)),None)
    headings={'技术领域','背景技术','发明内容','有益效果','附图说明','具体实施方式'}|{r[0] for r in roles}
    contract=result.get('document_contract_evidence',{})
    title_p=(contract.get('title') or {}).get('paragraph')
    claim_ps={p for c in claims for p in c['paragraphs']}
    first_description=title_p or next((p['p'] for p in body if p['text'].strip()=='技术领域'),None)
    explicit=None;seen_scopes={role(s.get('default_header','')) for s in out['sections']}
    for p in body:
        text=p['text'].strip();content=p.get('content_evidence',{})
        plain=content.get('plain_text','').strip();flow=p.get('page_flow',{})
        if p.get('body_direct') and text in {r[0] for r in roles}:explicit=role(text)
        section=role(p.get('default_header','')) or explicit
        if section is None and p['p'] in claim_ps:section='claims'
        if section is None and first_description is not None and p['p']>=first_description:section='description'
        if section:seen_scopes.add(section)
        locator={'part':'word/document.xml','paragraph':p['p'],'path':f'paragraph[{p["p"]}]'}
        objects=set(content.get('objects',[]));kind=content.get('kind')
        formats=p.get('word_run_formats',[])
        bold_candidate=bool(formats) and all(any(prop['name']=='b' and
            prop['attributes'].get('val','1') in {'1','true','on'}
            for prop in (group.get('properties_explicit') or [])) for group in formats)
        if kind=='empty':classification='section_carrier' if content.get('section_boundary') else 'empty'
        elif section=='abstract_figure':
            # A caption cannot bypass the abstract-image rule by using a heading
            # style. Only the literal section heading is a scope delimiter.
            classification=('heading' if p.get('body_direct',False) and text=='摘要附图' and not objects else
                'complex' if not p.get('body_direct',False) else
                'abstract_label' if plain else 'abstract_image' if objects=={'image_or_shape'} else 'complex')
        elif text in headings or p['p']==title_p or flow.get('heading_style') or benefit_introduction(text):classification='heading'
        elif section=='figures':classification='final_caption' if re.fullmatch(r'图\s*[1-9][0-9]*',plain) else 'final_image' if objects=={'image_or_shape'} and not plain else 'complex'
        elif section in {'claims','description'}:
            if kind=='display_formula':classification='display_formula'
            elif not p.get('body_direct',False):classification='complex'
            elif re.fullmatch(r'图\s*[1-9][0-9]*',plain):classification='caption'
            elif objects & {'ole','field','symbol','image_or_shape'}:classification='complex'
            elif not plain:classification='complex'
            # An OMML display equation may have an external ordinary-text
            # equation label. Preserve its keepLines until source-bound review.
            elif 'omml' in objects and not re.search(r'[\u3400-\u9fff]',plain):classification='complex'
            elif bold_candidate:classification='complex'
            elif not re.search(r'[\u3400-\u9fff]',plain) and re.search(r'[=≠≤≥∑∫]',plain):classification='complex'
            else:classification='ordinary'
        else:classification='outside_ordinary_scope'
        row=dict(locator,section=section,classification=classification,text=text[:120],
                 effective=flow.get('effective',{}),unresolved=flow.get('unresolved',[]),
                 content={k:v for k,v in content.items() if k!='plain_text'})
        out['paragraphs'].append(row)
        if kind=='empty':out['empty_candidates'].append(row)
        if classification=='abstract_label':out['abstract_labels'].append(dict(locator,text=plain))
        if classification=='final_caption':out['final_captions'].append(dict(locator,text=plain))
        if classification=='ordinary':
            for key in FLOW_DEFAULTS:
                value=flow.get('effective',{}).get(key,{})
                if value.get('value') is True:
                    out['ordinary_violations'].append(dict(locator,property=key,**value))
                elif value.get('value') is None:
                    out['review_candidates'].append(dict(locator,path=locator['path']+'/pPr/'+key,reason='unresolved_flow_property'))
            if content.get('manual_page_breaks'):
                out['ordinary_violations'].append(dict(locator,property='manual_page_break',value=True,source='direct'))
        if flow.get('unresolved') and section in {'claims','description'}:
            out['review_candidates'].append(dict(locator,path=locator['path']+'/pPr/style_chain',reason='unresolved_style_chain'))
        if classification=='complex' and section in {'claims','description','abstract_figure','figures'}:
            out['review_candidates'].append(dict(locator,reason='complex_body_content'))
        if section=='abstract_figure' and p.get('numbering_applied',{}).get('numId') not in {None,'0'}:
            out['review_candidates'].append(dict(locator,path=locator['path']+'/pPr/numPr',reason='generated_abstract_label'))
    # Alternate/unknown structures can be reviewed at their actual document
    # position. No unlocated global exception can stand in for these findings.
    for missing in sorted({'claims','description','abstract_figure','figures'}-seen_scopes):
        paragraph=body[0]['p'] if body else 0
        out['review_candidates'].append({'part':'word/document.xml','paragraph':paragraph,
            'path':f'paragraph[{paragraph}]/section_scope/{missing}','reason':'unresolved_section_scope'})
    for obj in part.get('body_objects',[]):
        if obj['element']!='tbl':
            out['review_candidates'].append({'part':'word/document.xml','paragraph':0,
                'path':obj['path'],'reason':'unparsed_body_object'})
    return out


def antecedent_occurrences(parts):
    """Expose exact locations; a literal short prefix is not noun segmentation."""
    rows = []
    for part in parts:
        if part['part'] == 'word/comments.xml':
            continue  # Comments are separately rejected from the clean final.
        for paragraph in part['paragraphs']:
            text = paragraph['text']
            for match in re.finditer('所述', text):
                following = text[match.end():]
                row = {'part': part['part'], 'paragraph': paragraph['p'],
                       'path': f'paragraph[{paragraph["p"]}]/visible_text',
                       'offset': match.start(), 'end_offset': match.end(),
                       'before': text[max(0, match.start()-40):match.start()],
                       'following': following[:80],
                       'status': 'requires_complete_head_noun_and_legal_prior_basis_review'}
                # Deliberately only a clue. For example, A的B can refer to A
                # or a complete compound; the actual head needs semantic review.
                short = re.match(r'([\u3400-\u4dbf\u4e00-\u9fff]{2})(?=$|[的，。；：、！？,.;:!?\s])', following)
                if short:
                    row['two_character_prefix_candidate'] = {
                        'text': short[1], 'offset': match.end(),
                        'end_offset': match.end()+2,
                        'status': 'literal_boundary_clue_not_a_noun_or_antecedent_verdict'}
                rows.append(row)
    return rows


def precheck(result, kind):
    issues = result['issues']
    def add(code, message, loc=None):
        issues.append({'code':code,'message':message,'locator':loc,'status':'needs_review'})
    body = next(p for p in result['parts'] if p['part']=='word/document.xml')['paragraphs']
    result['antecedent_occurrences'] = antecedent_occurrences(result['parts'])
    result['formula_count_status'] = 'unclassified_math_objects_not_source_formula_count'
    for row in result.get('text_color_evidence', {}).get('review_runs', []):
        add('TEXT_COLOR_REVIEW', 'Applied nonblack or unresolved font color; final patent prose and explanations must use clean black text. Resolve inherited/theme formatting and verify display; do not delete technical content.',
            f'{row["part"]} P{row["paragraph"]:04} run{row["run"]}')
    for formula in result['formulas']:
        locator = f'{formula["part"]} P{formula["paragraph"]:04} {formula["id"]}'
        layout = formula.get('math_evidence', {}).get('layout', {})
        if layout.get('single_equation_continuation_candidate'):
            add('MATH_SINGLE_EQUATION_CONTINUATION','Source-confirmed one equation must be reflowed into one line with readable local sizing; do not count display rows as separate equations.',locator)
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
    for row in result['antecedent_occurrences']:
        if row.get('two_character_prefix_candidate'):
            add('ANTECEDENT_SHORT_NOUN_REVIEW',
                'Rule v37: review the complete noun head and its independent prior introduction. '
                'A two-Han-character noun after 所述 is prohibited even with a prior occurrence; '
                'this prefix clue is not segmentation, does not cover all short nouns, and never '
                'authorizes substring replacement or a semantic pass.',
                f'{row["part"]} P{row["paragraph"]:04}:{row["offset"]}')
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
    result['document_contract_evidence']=document_contract_evidence(body)
    import invention_contract
    result['invention_evidence']=invention_contract.evidence(result,body,claims,result['document_contract_evidence']['title'])
    for hit in result['invention_evidence']['issues']:
        add('INVENTION_CLAIM_ALIGNMENT','Rule v27 fixed wording and current claim projection: '+hit['code'],str(hit.get('paragraph')))
    result['page_flow_evidence']=page_flow_evidence(result,body,claims)
    import figure_caption_contract
    result['figure_caption_evidence']=figure_caption_contract.evidence(result,body)
    for hit in result['figure_caption_evidence']['issues']:
        add('FINAL_FIGURE_CAPTION','Rule v38 final drawings allow images and number-only 图N captions: '+hit['code'],hit.get('path'))
    for hit in result['figure_caption_evidence']['review_candidates']:
        add('FINAL_FIGURE_INTERNAL_LABEL','Rule v38 graphic text requires source-bound visual identity; not an outside annotation.',hit['path'])
    for hit in result['page_flow_evidence']['ordinary_violations']:
        add('PAGE_FLOW_REVIEW','Rule v22 ordinary prose must permit natural page flow: '+hit['property']+' is enabled; preserve font, margins, grid and content.',
            f'{hit["part"]} {hit["path"]}')
    for hit in result['page_flow_evidence']['abstract_labels']:
        add('ABSTRACT_FIGURE_LABEL','Rule v22 abstract figure contains extra literal text/caption; keep final numbered figure captions in their own section.',
            f'{hit["part"]} {hit["path"]}')
    for hit in result['page_flow_evidence']['review_candidates']:
        add('PAGE_FLOW_STRUCTURE_REVIEW','Located structure requires the existing six-check review; '+hit['reason']+'. This does not prove a page is full or authorize content deletion.',
            f'{hit["part"]} {hit["path"]}')
    result['benefit_numbering_evidence']=benefit_numbering_evidence(body)
    for issue in result['benefit_numbering_evidence']['issues']:
        add('BENEFIT_NUMBERING_REVIEW','Rule v21: each effect prose paragraph needs consecutive literal 1.,2.,... labels. Keep the introduction unnumbered and preserve source formulas; '+issue['code'],
            'P'+str(issue['paragraph']) if 'paragraph' in issue else None)
    for hit in result.get('punctuation_evidence',{}).get('hits',[]):
        add('QUOTE_BRACKET_REVIEW','Final v19 prose must not contain quotation marks or brackets. Preserve formula content exactly; source-backed text formula spans require position-bound independent review, not automatic deletion.',
            f'{hit["part"]} {hit["path"]} {hit["codepoint"]}')
    for hit in result.get('punctuation_evidence',{}).get('preferred_wording_hits',[]):
        add('PREFERRED_WORDING_REVIEW','Rewrite this exact phrase to '+hit['replacement']+' while preserving surrounding technical nouns, conditions and formula identity; no blind replacement.',
            f'{hit["part"]} {hit["path"]} offset {hit["offset"]}')
    for hit in result.get('content_spacing_evidence',{}).get('hits',[]):
        add('CONTENT_LITERAL_WHITESPACE', 'Rule v26 final content must not contain literal spaces or padding. Rewrite multiword prose/code from its source or use the correct structure; never blindly concatenate names, alter formula constants or strip the entire XML. Natural math spacing and paragraph/style dimensions are not literal content.',
            f'{hit["part"]} {hit["path"]} offset {hit["offset"]} {hit["codepoint"]}')
    for part in result['parts']:
        if part['part']=='word/document.xml': continue
        for p in part['paragraphs']:
            for hit in re.finditer('从而|一一',p['text']):
                result['document_contract_evidence']['global_word_hits'].append({
                    'part':part['part'],'paragraph':p['p'],'path':f'paragraph[{p["p"]}]/visible_text',
                    'offset':hit.start(),'text':hit[0],
                    'formula_protected_candidate':any(s['start']<=hit.start() and s['end']>=hit.end() for s in p.get('formula_text_spans',[])),
                    'context':p['text'][max(0,hit.start()-24):hit.end()+32]})
    if result['wording_evidence']['occurrences']:
        add('WORDING_SCOPE_REVIEW','Review located wording candidates against claim-only restrictions, protected names/titles and whole-body replacements; never perform blind character replacement.')
    result['isolated_symbol_evidence']=[{'id':f['id'],'part':f['part'],'paragraph':f['paragraph'],'symbol':f['linear_text'],
        'status':'may_use_plain_upright_body_run_only_if_registered_bare_symbol_no_semantic_style'}
        for f in result['formulas'] if f.get('isolated_symbol_candidate')]
    start=anchors['附图说明']; end=anchors['具体实施方式']
    import figure_description_contract
    result['figure_description_evidence']=figure_description_contract.evidence(result,body)
    for hit in result['figure_description_evidence']['issues']:
        add('FIGURE_DESCRIPTION_WORDING','Rule v32: '+hit['code'],str(hit.get('paragraph')))
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
    report = directory/'inspection.json'
    report_bytes = None
    for row in records:
        relative = Path(row['file'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Cache artifact escaped generation')
        target = local_path(directory/relative)
        if not target.is_relative_to(directory) or target in files:
            raise ValueError('Duplicate or foreign cache artifact')
        if target == report:
            report_bytes = target.read_bytes()
            actual_sha = hashlib.sha256(report_bytes).hexdigest()
        else:
            actual_sha = file_sha(target)
        if actual_sha != row['sha256']:
            raise ValueError('Cache artifact changed: ' + row['file'])
        files[target] = row['sha256']
    if report not in files or directory/'content.txt' not in files:
        raise ValueError('Cache report/content missing')
    # Only the report needs parsing; retain exactly its verified bytes once.
    result = json.loads(report_bytes.decode('utf-8'))
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


def extractor_dependencies():
    import invention_contract
    import figure_description_contract
    import figure_caption_contract
    return {'invention_contract.py':file_sha(Path(invention_contract.__file__).resolve()),
            'figure_caption_contract.py':file_sha(Path(figure_caption_contract.__file__).resolve()),
            'single_line_math.py':file_sha(Path(single_line_math.__file__).resolve()),
            'formula_uniqueness.py':file_sha(Path(formula_uniqueness.__file__).resolve()),
            'figure_description_contract.py':file_sha(Path(figure_description_contract.__file__).resolve())}


def extractor_unchanged(identity):
    return file_sha(Path(__file__).resolve())==identity['extractor_sha256'] and extractor_dependencies()==identity.get('extractor_dependencies')


def cache_identity(input_path, kind, extract_media):
    source = local_path(input_path)
    return {'input': str(source), 'source_sha256': file_sha(source),
            'extractor_sha256': file_sha(Path(__file__).resolve()),
            'extractor_dependencies':extractor_dependencies(),
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
            if file_sha(source) != identity['source_sha256'] or not extractor_unchanged(identity):
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
    if file_sha(source) != identity['source_sha256'] or not extractor_unchanged(identity):
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
