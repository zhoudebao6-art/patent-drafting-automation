"""Opt-in presentation of one source equation; never a text reconstruction."""
from copy import deepcopy
import math
import re
from lxml import etree as ET

M='http://schemas.openxmlformats.org/officeDocument/2006/math'
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'m':M,'w':W}
LAYOUT={'baseJc','maxDist','objDist','rSp','rSpRule'}


def direct_text(children):
    return ''.join(''.join(n.text or '' for n in c.findall('./m:t',NS)) if c.tag=='{'+M+'}r' else '\ufffc' for c in children)


def _safe_array_rows(array):
    # The array and row wrappers alone are discarded; never lose unknown data.
    if (array.attrib or (array.text or '').strip() or (array.tail or '').strip()
            or len(array.findall('m:eqArrPr',NS))>1
            or any(c.tag not in {'{'+M+'}eqArrPr','{'+M+'}e'} for c in array)):return None
    props=array.find('m:eqArrPr',NS)
    if props is not None and (props.attrib or (props.text or '').strip() or (props.tail or '').strip() or any(
            c.tag not in {'{'+M+'}'+x for x in LAYOUT} or len(c)
            or set(c.attrib)-{'{'+M+'}val'} or (c.text or '').strip() or (c.tail or '').strip()
            for c in props)):
        return None  # Never discard unknown/control styles with the array shell.
    rows=array.findall('m:e',NS)
    if len(rows)<2 or any(r.attrib or not len(r) or (r.text or '').strip() or (r.tail or '').strip() for r in rows):return None
    return rows


def continuation_rows(node):
    children=[c for c in node if c.tag!='{'+M+'}oMathPr']
    if len(children)!=1 or children[0].tag!='{'+M+'}eqArr':return None
    rows=_safe_array_rows(children[0])
    if rows is None:return None
    texts=[direct_text(list(r)) for r in rows]
    if any(re.search(r'[\u3400-\u9fff]|\b(?:if|when|otherwise)\b',s) for s in texts):return None
    if any(re.search(r'[,，;；]',s) for s in texts[:-1]):return None
    if re.search(r'[,，;；].+',texts[-1]):return None
    relations=[re.findall(r'[=≠≤≥≈≔<>]',s) for s in texts]
    if len(relations[0])!=1 or relations[0][0]!='=' or any(relations[1:]):return None
    lhs,rhs=texts[0].split('=',1)
    if not lhs or not texts[-1].rstrip(';；'):return None
    for i,s in enumerate(texts[1:],1):
        if (i>1 or rhs) and not s.startswith(('+','−','-')):return None
        if not s.strip('+−-;；'):return None
    return rows


def set_builder_rows(node):
    """A conservative display-only candidate, still requiring source review."""
    children=[c for c in node if c.tag!='{'+M+'}oMathPr']
    delimiters=[(i,c) for i,c in enumerate(children) if c.tag=='{'+M+'}d']
    if len(delimiters)!=1:return None
    index,delimiter=delimiters[0]
    prefix=direct_text(children[:index])
    suffix=direct_text(children[index+1:])
    if (not prefix.endswith('=') or len(re.findall(r'[=≠≤≥≈≔<>]',prefix))!=1
            or not prefix[:-1] or suffix not in {'',';','；'}):return None
    if (len(delimiter.findall('m:dPr',NS))!=1 or len(delimiter.findall('m:e',NS))!=1
            or any(c.tag not in {'{'+M+'}dPr','{'+M+'}e'} for c in delimiter)):return None
    props=delimiter.find('m:dPr',NS)
    for name,value in [('begChr','{'),('endChr','}')]:
        marks=props.findall('m:'+name,NS)
        if len(marks)!=1 or marks[0].get('{'+M+'}val')!=value:return None
    expression=delimiter.find('m:e',NS)
    if len(expression)!=1 or expression[0].tag!='{'+M+'}eqArr':return None
    rows=_safe_array_rows(expression[0])
    if rows is None:return None
    texts=[direct_text(list(row)) for row in rows]
    if any(re.search(r'[\u3400-\u9fff]|\b(?:if|when|otherwise)\b',s,re.I) for s in texts):return None
    head=re.split('[|∣]',texts[0],maxsplit=1)
    if len(head)!=2 or '∈' not in head[0] or not all(head[0].split('∈',1)):return None
    if re.search(r'[=≠≤≥≈≔<>:：;；]',head[0]):return None
    # Join only an already connected predicate: never invent a conjunction.
    if not head[1] or any(not s for s in texts):return None
    for previous,current in zip(texts,texts[1:]):
        if not (previous.endswith((':','：','∧','∨')) or current.startswith(('∧','∨'))):return None
        if not current.strip('∧∨:：'):return None
    if texts[-1].endswith((':','：','∧','∨')):return None
    return rows


def render(node,options,equation_count):
    if (not isinstance(options,dict) or set(options)!={'classification','font_size_pt','source_base_pt'}
            or options['classification'] not in ('single_equation','set_builder') or equation_count!=1):
        raise ValueError('single_line requires a source-confirmed single_equation or set_builder and equation_count=1')
    size,base=options['font_size_pt'],options['source_base_pt']
    if (any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (size,base))
            or not 9<=size<=14 or not size<=base<=72 or size*2!=int(size*2)):
        raise ValueError('single_line uses readable half-point sizes 9..14, no larger than source_base_pt')
    children=[c for c in node if c.tag!='{'+M+'}oMathPr']
    if options['classification']=='set_builder':
        if set_builder_rows(node) is None:
            raise ValueError('set_builder requires one closed-brace membership/filter continuation with existing connectors')
        result=deepcopy(node)
        rows=set_builder_rows(result)
        array=rows[0].getparent()
        expression=array.getparent()
        for row in rows:
            for child in row:expression.append(deepcopy(child))
        expression.remove(array)
        direct_runs=result.findall('./m:r',NS)+expression.findall('./m:r',NS)
    elif any(c.tag=='{'+M+'}eqArr' for c in children):
        rows=continuation_rows(node)
        if rows is None:raise ValueError('Not a supported pure display continuation; preserve groups, conditions and unknown styles')
        result=deepcopy(node)
        for child in list(result):
            if child.tag!='{'+M+'}oMathPr':result.remove(child)
        for row in rows:
            for child in row:result.append(deepcopy(child))
    else:
        text=direct_text(children)
        if len(re.findall(r'[=≠≤≥≈≔<>]',text))!=1 or '=' not in text:
            raise ValueError('single_line needs one complete outer equality')
        lhs,rhs=text.rstrip(';；').split('=',1)
        if not lhs or not rhs:raise ValueError('single_line equation has an empty side')
        result=deepcopy(node)
    # Remove alignment/break markers only from root runs or the selected rows.
    # Deeper breaks still require a separate source review.
    if options['classification']!='set_builder':direct_runs=result.findall('./m:r',NS)
    for run in direct_runs:
        props=run.find('m:rPr',NS)
        if props is not None:
            for p in list(props):
                if p.tag in {'{'+M+'}brk','{'+M+'}aln'}:props.remove(p)
    if result.findall('.//m:brk',NS) or result.findall('.//w:br',NS) or result.findall('.//w:tab',NS):
        raise ValueError('Nested breaks cannot be removed by single_line')
    ratio=size/base
    # Direct source sizes retain their proportions; script structures, font
    # families, bold/script identity, spacing and operator limits are untouched.
    for tag in ('sz','szCs'):
        for prop in result.findall('.//w:'+tag,NS):
            value=prop.get('{'+W+'}val','')
            if not value.isdigit() or int(value)<1:raise ValueError('Unresolved source math size')
            prop.set('{'+W+'}val',str(max(1,round(int(value)*ratio))))
    for owner in [*result.findall('.//m:r',NS),*result.findall('.//m:ctrlPr',NS)]:
        props=owner.find('w:rPr',NS)
        if props is None:
            props=ET.Element('{'+W+'}rPr')
            owner.insert(1 if len(owner) and owner[0].tag=='{'+M+'}rPr' else 0,props)
        for tag in ('sz','szCs'):
            if props.find('w:'+tag,NS) is None:
                other=props.find('w:'+('szCs' if tag=='sz' else 'sz'),NS)
                value=other.get('{'+W+'}val') if other is not None else str(int(size*2))
                ET.SubElement(props,'{'+W+'}'+tag).set('{'+W+'}val',value)
    return result
