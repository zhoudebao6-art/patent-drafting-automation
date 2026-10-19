"""Conservative structural identities for complete native equations.

This is not algebraic equivalence or source formula counting. Text, image and
OLE formulas and differently encoded identities retain source-bound review.
"""
import hashlib
import json

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
RELATIONS = frozenset('=≠≈≃≅≡≤≥<>≲≳∝∈∉⊆⊂⊇⊃')
TERMINAL = frozenset(',;，；。')
LAYOUT = frozenset(('oMathPr', 'ctrlPr', 'brk', 'aln', 'alnScr', 'argSz',
                    'cGp', 'cGpRule', 'cSp', 'rSp', 'rSpRule', 'baseJc',
                    'jc', 'maxDist', 'objDist', 'plcHide', 'wrapIndent'))


def _name(node):
    return node.tag.split('}')[-1]


def _val(node):
    return node.get('{'+M+'}val', node.get('{'+W+'}val', '1'))


def _true(value):
    return value not in ('0', 'false', 'off')


def _run_style(node):
    # Bold and mathematical alphabet families can distinguish quantities.
    # Typeface, size, colour, italic slant and spacing are display choices.
    word_bold = False
    math_bold = None
    script = 'roman'
    for props in node:
        if _name(props) != 'rPr':
            continue
        for prop in props:
            name = _name(prop)
            if name == 'sty':
                math_bold = _val(prop) in ('b', 'bi')
            elif name == 'b':
                word_bold = _true(_val(prop))
            elif name == 'scr':
                script = _val(prop)
    return [word_bold if math_bold is None else math_bold, script]


def _trim(items):
    while items and items[-1][0] == 'char' and items[-1][2] in TERMINAL:
        items.pop()
    return items


def _children(node, trim=False):
    items = []
    for child in node:
        items.extend(_canonical(child))
    return _trim(items) if trim else items


def _canonical(node):
    name = _name(node)
    if node.tag.startswith('{'+W+'}') or name in LAYOUT:
        return []
    if name == 'rPr':
        return []
    if name == 'r':
        style = _run_style(node)
        return [['char', style, char] for text in node.iter('{'+M+'}t')
                for char in (text.text or '') if not char.isspace()]
    if name in ('oMath', 'oMathPara'):
        return _children(node, trim=True)
    if name == 'eqArr':
        return [['eqArr', [_children(child, trim=True) for child in node
                           if child.tag == '{'+M+'}e']]]
    attrs = sorted((key, value) for key, value in node.attrib.items()
                   if not key.startswith('{'+W+'}'))
    children = _children(node)
    # Empty property holders are formatting residue, not mathematical nodes.
    if name.endswith('Pr') and not attrs and not children:
        return []
    return [[name, attrs, children]]


def _relation(items):
    # Only a relation at this expression level establishes completeness.
    # A condition inside min_{x in A}, a superscript or argument does not.
    for i, token in enumerate(items):
        if (token[0] == 'char' and token[2] in RELATIONS and
                i > 0 and i < len(items)-1):
            return True
    return any(token[0] == 'eqArr' and token[1] and
               all(_relation(row) for row in token[1]) for token in items)


def equation_fingerprint(node):
    """Return a SHA-256 identity for a complete relation/group, else None.

    Accepts either ElementTree or lxml OMML nodes. Presentation-only run
    splitting, terminal equation punctuation and layout are ignored. No
    Unicode compatibility folding, operand reorder or algebraic rewrite occurs.
    """
    text = ''.join(text.text or '' for text in node.iter('{'+M+'}t'))
    if not any(char.isalpha() or char in '∇∂∞' for char in text):
        return None  # Pure numerical worked arithmetic is not a source formula.
    items = _children(node, trim=True) if _name(node) == 'e' else _canonical(node)
    if not _relation(items):
        return None
    return hashlib.sha256(json.dumps(items, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()
