"""Final drawing pages contain images and literal number-only captions.

Reuse extracted paragraphs. This is not an image-content or visual verdict.
"""
import re

MODE = 'number_only_v38'
LABEL_ROLES = {'axis_label', 'axis_unit', 'legend', 'flow_node', 'technical_mark'}


def evidence(report, body):
    out = dict(schema_version=1, artifact_sha256=report['sha256'], complete=True,
               scope_resolved=False, captions=[], images=[], issues=[], review_candidates=[])
    # Lock the final drawing area to the end: a later styled heading cannot
    # disguise an annotation as ordinary prose or restart another section.
    starts = [p['p'] for p in body if
                  re.sub(r'\s+', '', p.get('default_header', '')) == '说明书附图' or
                  (p.get('body_direct') and p['text'].strip() == '说明书附图')]
    starts.extend(s['start_paragraph'] for s in report.get('page_flow_evidence', {}).get('sections', [])
                  if re.sub(r'\s+', '', s.get('default_header', '')) == '说明书附图')
    start = min(starts) if starts else None
    if start is None:
        out['issues'].append(dict(code='final_figure_scope_unresolved'))
        return out
    out['scope_resolved'] = True
    for p in body:
        if p['p'] < start:
            continue
        loc = dict(part='word/document.xml', paragraph=p['p'], path=f'paragraph[{p["p"]}]')
        content = p.get('content_evidence', {})
        container = p.get('container_evidence')
        if not isinstance(container, dict):
            out['issues'].append(dict(loc, code='missing_container_evidence'))
            continue
        if container.get('deleted'):
            continue
        text = content.get('plain_text', '')
        objects = set(content.get('objects', []))
        numbered = p.get('numbering_applied', {}).get('numId') not in (None, '0')
        if container.get('in_graphic'):
            # A floating textbox may actually be an outside annotation. Only a
            # located, source-bound visual finding can establish its identity.
            if text or objects:
                out['review_candidates'].append(dict(loc, text=text or p['text'], reason='graphic_text_or_object'))
            continue
        for i, label in enumerate(content.get('graphic_text', []), 1):
            out['review_candidates'].append(dict(loc, path=loc['path']+f'/graphic_text[{i}]',
                                                  text=label, reason='graphic_text_or_object'))
        if p['p'] == start and p.get('body_direct') and text == '说明书附图' and not objects and not numbered:
            continue
        if not text.strip() and not objects and not numbered:
            continue  # Empty structural carriers remain in the existing layout review.
        caption = re.fullmatch(r'图([1-9][0-9]*)', text)
        if caption and not objects and not numbered:
            out['captions'].append(dict(loc, text=text, number=int(caption[1])))
        elif not text.strip() and objects == {'image_or_shape'} and not numbered:
            out['images'].append(loc)
        else:
            out['issues'].append(dict(loc, text=text, code='external_figure_content'))
    numbers = [c['number'] for c in out['captions']]
    if (out['images'] and not numbers) or numbers != list(range(1, len(numbers)+1)):
        out['issues'].append(dict(code='final_caption_sequence'))
    if numbers and not out['images']:
        out['issues'].append(dict(code='final_figure_image_missing'))
    return out


def validate(report, findings=(), source_hashes=()):
    ev = report.get('figure_caption_evidence')
    if (not isinstance(ev, dict) or ev.get('schema_version') != 1 or
            ev.get('complete') is not True or ev.get('scope_resolved') is not True or
            ev.get('artifact_sha256') != report.get('sha256') or
            any(not isinstance(ev.get(k), list) for k in ('captions', 'images', 'issues', 'review_candidates'))):
        raise ValueError('Final figure caption evidence missing, unresolved or bound to another artifact')
    if ev['issues']:
        raise ValueError('Final figure caption must contain only 图N outside the image: '+
                         ', '.join(h['code']+' '+h.get('path', '') for h in ev['issues'][:6]))
    numbers = {c['number'] for c in ev['captions']}
    for hit in ev['review_candidates']:
        if not any(isinstance(f, dict) and f.get('rule_id') == 'TXT-11' and
                   f.get('classification') == 'figure_internal_label' and f.get('status') == 'closed' and
                   f.get('artifact_sha256') == report['sha256'] and
                   all(f.get(k) == hit.get(k) for k in ('part', 'paragraph', 'path', 'text')) and
                   type(f.get('figure_number')) is int and f['figure_number'] in numbers and
                   f.get('label_role') in LABEL_ROLES and f.get('source_sha256') in source_hashes and
                   all(isinstance(f.get(k), str) and f[k].strip() for k in
                       ('source_locator', 'registry_id', 'visual_locator', 'reason'))
                   for f in findings):
            raise ValueError('Final figure graphic label needs a located source-bound visual finding: '+hit['path'])
