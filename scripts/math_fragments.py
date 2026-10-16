"""Source-bound presentation helpers; never infer technical definitions."""
from copy import deepcopy
import re
from lxml import etree as ET

M='http://schemas.openxmlformats.org/officeDocument/2006/math'
NS={'m':M}
SYMBOLS={'r','sSub','sSup','sSubSup','sPre','acc','bar','groupChr','func'}
ATTACHED=SYMBOLS|{'limLow','limUpp','phant','borderBox','box'}


def located_node(node, path):
    """Resolve only explicit indexed paths; never search by displayed text."""
    if not isinstance(path,str) or not re.fullmatch(r'\./m:[A-Za-z][A-Za-z0-9]*\[[1-9][0-9]*\](?:/m:[A-Za-z][A-Za-z0-9]*\[[1-9][0-9]*\])*',path):
        raise ValueError('Source symbol path must be an exact indexed math path')
    selected=node.xpath(path,namespaces=NS)
    if len(selected)!=1:
        raise ValueError('Source symbol path must identify one complete symbol structure')
    return selected[0]


def symbol_fragment(node, path):
    """Copy an explicitly selected complete source quantity, not its bare base."""
    paths=path if isinstance(path,list) else [path]
    if not paths:raise ValueError('Source symbol paths must not be empty')
    parts=[located_node(node,p) for p in paths]
    if len(parts)>1:
        parent=parts[0].getparent()
        if (any(p.getparent() is not parent for p in parts)
                or any(a.getnext() is not b for a,b in zip(parts,parts[1:]))):
            raise ValueError('Composite symbol paths must be consecutive siblings in source order')
    def call_head(part):
        if part is None:return False
        base=part
        while ET.QName(base).localname in ATTACHED-{'r','func'}:
            base=base.find('m:e',NS)
            if base is None:return False
            children=[n for n in base if not ET.QName(n).localname.endswith('Pr')]
            if len(children)!=1:return False
            base=children[0]
        return base.tag=='{'+M+'}r' and ''.join(base.xpath('./m:t/text()',namespaces=NS)).isalpha()
    for part in parts:
        kind=ET.QName(part).localname
        next_node=part.getnext()
        followed_by_argument=call_head(part) and next_node is not None and next_node.tag=='{'+M+'}d'
        if followed_by_argument and next_node not in parts:
            raise ValueError('Source quantity head needs its complete adjacent argument; ambiguous products require reviewed manual representation')
        if kind=='d':
            previous=part.getprevious()
            if previous not in parts or not call_head(previous) or not part.findall('m:e',NS) or any(
                    not list(entry) for entry in part.findall('m:e',NS)):
                raise ValueError('A source function argument requires its complete adjacent head and nonempty arguments')
        elif kind not in SYMBOLS:
            raise ValueError('Source symbol path must identify one complete symbol structure')
        for ancestor in part.iterancestors():
            if ancestor is node:break
            # Complete quantities inside a function argument may be explained
            # separately. The function name alone must not stand for the call.
            if ET.QName(ancestor).localname in (ATTACHED-{'func'})|{'fName'}:
                raise ValueError('Source symbol path omits its enclosing subscript, superscript or modifier')
        if kind=='r':
            text=''.join(part.xpath('./m:t/text()',namespaces=NS))
            if not text.isalpha() or (len(text)!=1 and not followed_by_argument):
                raise ValueError('A flat source symbol must be one actual letter or a complete function head with arguments')
        if kind=='func' and any(part.find('m:'+slot,NS) is None or not len(part.find('m:'+slot,NS))
                                for slot in ('fName','e')):
            raise ValueError('A complete function needs its source name and argument structure')
    result=ET.Element('{'+M+'}oMath',nsmap=node.nsmap)
    for prop in node.findall('m:oMathPr',NS):result.append(deepcopy(prop))
    for part in parts:result.append(deepcopy(part))
    return result


def operator_reference(node, name_path, argument_path):
    """Render a located unary operator as name(·), never change its real use."""
    name=located_node(node,name_path);argument=located_node(node,argument_path)
    if (name.tag!='{'+M+'}r' or argument.tag!='{'+M+'}d'
            or name.getnext() is not argument
            or not re.fullmatch(r'[A-Za-z]+',''.join(name.xpath('./m:t/text()',namespaces=NS)))):
        raise ValueError('Operator reference needs an adjacent source name and argument delimiter')
    for parent in name.iterancestors():
        if parent is node:break
        if ET.QName(parent).localname in ATTACHED:
            raise ValueError('Operator reference cannot truncate an enclosing modifier')
    entries=argument.findall('m:e',NS)
    if len(entries)!=1 or not list(entries[0]):
        raise ValueError('Only a located unary operator argument can use a placeholder')
    for prop,default in [('begChr','('),('endChr',')')]:
        value=argument.find('m:dPr/m:'+prop,NS)
        if value is not None and value.get('{'+M+'}val',default)!=default:
            raise ValueError('Operator reference requires round argument delimiters')
    # A top-level comma denotes more than one argument; nested delimiters keep
    # their own arguments and do not establish operator arity here.
    if any(re.search('[,，;；]',t) for t in entries[0].xpath('./m:r/m:t/text()',namespaces=NS)):
        raise ValueError('Multiple operator arguments require an explicit reviewed representation')
    result=ET.Element('{'+M+'}oMath',nsmap=node.nsmap)
    for prop in node.findall('m:oMathPr',NS):result.append(deepcopy(prop))
    label=deepcopy(name)
    delimiter=deepcopy(argument);entry=delimiter.find('m:e',NS)
    for child in list(entry):entry.remove(child)
    # Only the newly introduced placeholder has a presentation of its own.
    # The source operator name must retain all its original style properties.
    dot=deepcopy(label)
    props=dot.find('m:rPr',NS)
    if props is None:props=ET.Element('{'+M+'}rPr');dot.insert(0,props)
    style=props.find('m:sty',NS)
    if style is None:style=ET.SubElement(props,'{'+M+'}sty')
    style.set('{'+M+'}val','b' if style.get('{'+M+'}val') in {'b','bi'} else 'p')
    # Word run italic overrides can otherwise conflict with the math style.
    for italic in dot.xpath('./w:rPr/w:i | ./w:rPr/w:iCs',namespaces={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}):
        italic.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val','0')
    for child in list(dot):
        if child.tag=='{'+M+'}t':dot.remove(child)
    ET.SubElement(dot,'{'+M+'}t').text='·'
    entry.append(dot);result.extend([label,delimiter])
    return result


def equation_group_punctuation(node, equation_count):
    """Punctuate a declared group of complete top-level display equations."""
    children=[n for n in node if n.tag!='{'+M+'}oMathPr']
    if equation_count<2 or len(children)!=1 or children[0].tag!='{'+M+'}eqArr':return node
    rows=children[0].findall('m:e',NS)
    if len(rows)!=equation_count:
        raise ValueError('Complete equation group row count differs from declared equation_count')
    for row in rows:
        text=''.join(''.join(c.xpath('./m:t/text()',namespaces=NS))
                     if c.tag=='{'+M+'}r' else '\ufffc' for c in row)
        if (any(n.tag=='{'+M+'}m' for n in row.iter())
                or any(n.get('{'+M+'}val') in {'{','｛'} for n in row.findall('.//m:begChr',NS))):
            raise ValueError('Matrix or conditional groups require a reviewed presentation')
        if re.search(r'[,，;；]{2,}$',text):
            raise ValueError('Repeated terminal group punctuation needs source review')
        relations=list(re.finditer(r'[=≠≤≥≈≔<>]',text))
        if (len(relations)!=1 or re.search(r'[\u3400-\u9fff]|[,，;；].+[^,，;；]$',text)
                or not text[:relations[0].start()].strip(' ,，;；')
                or not text[relations[0].end():].strip(' ,，;；')):
            raise ValueError('Group punctuation requires complete equations, not conditions or continuations')
    result=deepcopy(node)
    for i,row in enumerate(result.findall('m:eqArr/m:e',NS)):
        mark='，' if i<len(rows)-1 else '；'
        tail=row[-1] if len(row) else None
        texts=tail.findall('m:t',NS) if tail is not None and tail.tag=='{'+M+'}r' else []
        textnode=next((n for n in reversed(texts) if n.text),None)
        if textnode is not None and textnode.text[-1:] in {',','，',';','；'}:
            # Keep an already correct ASCII/Chinese punctuation character.
            if textnode.text[-1:] not in ({',','，'} if i<len(rows)-1 else {';','；'}):
                textnode.text=textnode.text[:-1]+mark
        else:
            run=ET.SubElement(row,'{'+M+'}r')
            props=ET.SubElement(run,'{'+M+'}rPr')
            ET.SubElement(props,'{'+M+'}sty').set('{'+M+'}val','p')
            ET.SubElement(run,'{'+M+'}t').text=mark
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
