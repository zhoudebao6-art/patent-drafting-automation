"""Source-bound presentation helpers; never infer technical definitions."""
from copy import deepcopy
import re
from lxml import etree as ET

M='http://schemas.openxmlformats.org/officeDocument/2006/math'
NS={'m':M}
SYMBOLS={'r','sSub','sSup','sSubSup','sPre','acc','bar','groupChr'}
ATTACHED=SYMBOLS|{'limLow','limUpp','phant','borderBox','box'}


def symbol_fragment(node, path):
    """Copy an explicitly selected complete source quantity, not its bare base."""
    if not isinstance(path,str) or not re.fullmatch(r'\./m:[A-Za-z][A-Za-z0-9]*\[[1-9][0-9]*\](?:/m:[A-Za-z][A-Za-z0-9]*\[[1-9][0-9]*\])*',path):
        raise ValueError('Source symbol path must be an exact indexed math path')
    selected=node.xpath(path,namespaces=NS)
    if len(selected)!=1 or ET.QName(selected[0]).localname not in SYMBOLS:
        raise ValueError('Source symbol path must identify one complete symbol structure')
    part=selected[0]
    for ancestor in part.iterancestors():
        if ancestor is node:break
        if ET.QName(ancestor).localname in ATTACHED:
            raise ValueError('Source symbol path omits its enclosing subscript, superscript or modifier')
    if ET.QName(part).localname=='r':
        text=''.join(part.xpath('./m:t/text()',namespaces=NS))
        if len(text)!=1 or not text.isalpha():
            raise ValueError('A flat source symbol must be one actual letter')
    result=ET.Element('{'+M+'}oMath',nsmap=node.nsmap)
    for prop in node.findall('m:oMathPr',NS):result.append(deepcopy(prop))
    result.append(deepcopy(part))
    return result


def stacked_equations(node, breaks, equation_count):
    """Wrap complete top-level equation rows; preserve every source child."""
    children=[n for n in node if n.tag!='{'+M+'}oMathPr']
    if (not isinstance(breaks,list) or not breaks or any(type(v) is not int for v in breaks)
            or breaks!=sorted(set(breaks)) or breaks[0]<1 or breaks[-1]>=len(children)):
        raise ValueError('display_breaks must be increasing source-child boundaries')
    if equation_count != len(breaks)+1:
        raise ValueError('Declare the actual complete equation_count for stacked equations')
    if any(n.tag=='{'+M+'}eqArr' for n in children):
        raise ValueError('Existing equation arrays require source classification, not nested wrapping')
    cuts=[0,*breaks,len(children)]
    rows=[children[start:end] for start,end in zip(cuts,cuts[1:])]
    for i,row in enumerate(rows):
        # A relation in an index or function argument cannot establish that the
        # entire row is an equation. Structured operands remain opaque here.
        text=''.join(''.join(child.xpath('./m:t/text()',namespaces=NS))
                     if child.tag=='{'+M+'}r' else '\ufffc' for child in row)
        relation=re.search(r'[=≠≤≥≈≔<>]',text)
        if (relation is None or not text[:relation.start()].strip(' ,，;；')
                or not text[relation.end():].strip(' ,，;；')):
            raise ValueError('Each stacked row must retain a complete source relation')
        if i<len(rows)-1:
            tail=row[-1]
            if tail.tag!='{'+M+'}r' or ''.join(tail.xpath('./m:t/text()',namespaces=NS)) not in {',','，',';','；'}:
                raise ValueError('Split only after an explicit top-level equation separator')
    result=ET.Element('{'+M+'}oMath',nsmap=node.nsmap)
    for prop in node.findall('m:oMathPr',NS):result.append(deepcopy(prop))
    array=ET.SubElement(result,'{'+M+'}eqArr')
    props=ET.SubElement(array,'{'+M+'}eqArrPr')
    ET.SubElement(props,'{'+M+'}baseJc').set('{'+M+'}val','center')
    for row in rows:
        entry=ET.SubElement(array,'{'+M+'}e')
        for child in row:entry.append(deepcopy(child))
    return result
