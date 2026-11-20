"""Bounded, read-only views of an existing inspection; no DOCX reparse or approval."""
import argparse
import hashlib
import json
from pathlib import Path


def field_catalog(report):
    """List array paths/counts without echoing document text or mathematical trees."""
    fields = {}
    def visit(node, prefix=''):
        for key, value in node.items():
            if not isinstance(key, str) or '.' in key:
                continue
            name = prefix + key
            if isinstance(value, list):
                fields[name] = len(value)
            elif isinstance(value, dict):
                visit(value, name+'.')
    visit(report)
    return fields


def _page(rows, offset, limit):
    end = offset + limit
    return {'total': len(rows), 'offset': offset, 'data': rows[offset:end],
            'next_offset': end if end < len(rows) else None}


def query(path, section='summary', ids=None, part='word/document.xml', start=None, end=None, offset=0, limit=30, field=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('Independent inspection JSON required')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Offset/limit outside supported range')
    raw = path.read_bytes(); report = json.loads(raw.decode('utf-8-sig'))
    result = {'inspection': str(path.resolve()), 'inspection_sha256': hashlib.sha256(raw).hexdigest(),
              'input': report['input'], 'source_sha256': report['sha256'],
              'status': 'navigation_only_not_semantic_approval', 'section': section}
    if field is not None and section != 'field':
        raise ValueError('Field path requires section=field')
    if section == 'summary':
        result['data'] = {k: report.get(k) for k in ('formula_count_status', 'claims_detected', 'required_semantic_reviews', 'review_status')}
        result['counts'] = {'formulas': len(report.get('formulas', [])), 'issues': len(report.get('issues', [])),
                            'parts': len(report.get('parts', [])), 'media': len(report.get('media', []))}
        result['available_fields'] = field_catalog(report)
        return result
    if section == 'formulas':
        rows = report.get('formulas', [])
        if ids:
            if len(ids) != len(set(ids)) or set(ids) - {r['id'] for r in rows}:
                raise ValueError('Unknown or duplicated math object ID')
            rows = [r for r in rows if r['id'] in ids]
    elif section == 'paragraphs':
        if start is None or end is None or not 1 <= start <= end:
            raise ValueError('An explicit inclusive paragraph range is required')
        parts = [r for r in report.get('parts', []) if r['part'] == part]
        if len(parts) != 1: raise ValueError('Unknown or duplicated document part')
        rows = [r for r in parts[0]['paragraphs'] if start <= r['p'] <= end]
        result['part'] = part
    elif section == 'issues':
        rows = report.get('issues', [])
    elif section == 'field':
        names = field if isinstance(field, (list, tuple)) else [field]
        catalog = field_catalog(report)
        if not names or any(not isinstance(name, str) or name not in catalog for name in names):
            raise ValueError('Unknown array field; inspect available_fields first')
        if len(set(names)) != len(names):
            raise ValueError('Duplicated array field')
        pages = {}
        for name in names:
            rows = report
            for key in name.split('.'):
                rows = rows[key]
            pages[name] = _page(rows, offset, limit)
        if len(names) == 1:
            result.update(field=names[0], **pages[names[0]])
        else:
            result['fields'] = pages
        return result
    else:
        raise ValueError('Unknown inspection section')
    result.update(_page(rows, offset, limit))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inspection', type=Path)
    parser.add_argument('--section', choices=['summary','formulas','paragraphs','issues','field'], default='summary')
    parser.add_argument('--field', action='append', help='Exact array path; repeat for several fields from the same inspection, with separate page totals and cursors')
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--part', default='word/document.xml')
    parser.add_argument('--start', type=int); parser.add_argument('--end', type=int)
    parser.add_argument('--offset', type=int, default=0); parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    try:
        print(json.dumps(query(args.inspection, args.section, args.ids, args.part, args.start, args.end,
                               args.offset, args.limit, args.field), ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, 'Evidence query refused: '+str(exc)+'\n')


if __name__ == '__main__': main()
