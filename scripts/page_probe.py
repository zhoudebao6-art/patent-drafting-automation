"""Locate one original page/crop for preview doubts; never grant visual acceptance."""
import argparse
import io
import json
from pathlib import Path
from PIL import Image
import batch_tool as b

REGIONS = {'full': (0, 1), 'header': (0, .18), 'footer': (.82, 1), 'body': (.12, .92)}


def probe(render_file, source, page, *, region='full', crop_dir=None,
          evidence_sha256=None, pdf_text=False):
    render_file = Path(render_file).resolve(strict=True)
    root = render_file.parent
    source = Path(source).resolve(strict=True)
    initial = b.sha(render_file)
    if evidence_sha256 is not None and initial != evidence_sha256:
        raise ValueError('Render evidence differs from the supplied SHA')
    evidence = b.read(render_file)
    if evidence.get('status') != 'rendered_pending_visual_review' or evidence.get('input_sha256') != b.sha(source):
        raise ValueError('Render is incomplete or belongs to different DOCX bytes')
    if evidence.get('input') and Path(evidence['input']).resolve() != source:
        raise ValueError('Render belongs to a different artifact path')
    pages = evidence.get('pages', [])
    if type(page) is not int or not 1 <= page <= len(pages) or region not in REGIONS:
        raise ValueError('Invalid page or region')
    row = pages[page-1]
    if row['file'] != f'page-{page}.png':
        raise ValueError('Selected page identity is inconsistent')
    original = b.clean_path(root/row['file'], root)
    pdf = b.clean_path(root/evidence['pdf']['file'], root)
    if b.sha(original) != row['sha256'] or b.sha(pdf) != evidence['pdf']['sha256']:
        raise ValueError('Selected page or PDF changed')
    with Image.open(original) as image:
        image.verify()
    with Image.open(original) as image:
        width, height = image.size
        lo, hi = REGIONS[region]
        box = (0, int(height*lo), width, int(height*hi))
        result = {'page': page, 'artifact_sha256': evidence['input_sha256'],
                  'render_evidence': str(render_file), 'render_evidence_sha256': initial,
                  'original_png': str(original), 'original_png_sha256': row['sha256'],
                  'pdf': str(pdf), 'pdf_sha256': evidence['pdf']['sha256'],
                  'pixel_size': [width, height], 'visual_review': 'not_run',
                  'scope': 'Selected page bytes and optional unscaled crop only; not full-render or visual acceptance'}
        if region != 'full':
            if crop_dir is None:
                raise ValueError('A separate crop directory is required')
            target_root = Path(crop_dir).absolute()
            if b.inside(target_root, root):
                raise ValueError('Do not write derivatives into original render evidence')
            target_root.mkdir(parents=True, exist_ok=True)
            target = b.clean_path(target_root/f'p{page}-{region}-{row["sha256"][:10]}.png', target_root)
            buffer = io.BytesIO()
            image.crop(box).save(buffer, format='PNG')
            raw = buffer.getvalue()
            try:
                with target.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError:
                if target.read_bytes() != raw:
                    raise ValueError('Existing crop differs; original evidence was preserved')
            result.update(crop=str(target), crop_sha256=b.sha(target), pixel_box=list(box), rescaled=False)
    if pdf_text:
        from pypdf import PdfReader
        document = PdfReader(pdf)
        if len(document.pages) != len(pages):
            raise ValueError('PDF and render page counts differ')
        text = document.pages[page-1].extract_text() or ''
        result.update(pdf_text=text[:12000], pdf_text_truncated=len(text)>12000,
                      text_scope='Same PDF page text is supporting evidence, not a visual verdict')
    if (b.sha(render_file) != initial or b.sha(source) != evidence['input_sha256']
            or b.sha(original) != row['sha256'] or b.sha(pdf) != evidence['pdf']['sha256']):
        raise ValueError('Evidence changed during page probing')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--region', choices=REGIONS, default='full')
    parser.add_argument('--crop-dir')
    parser.add_argument('--evidence-sha256')
    parser.add_argument('--pdf-text', action='store_true')
    args = parser.parse_args()
    print(json.dumps(probe(args.render, args.input, args.page, region=args.region,
                           crop_dir=args.crop_dir, evidence_sha256=args.evidence_sha256,
                           pdf_text=args.pdf_text), ensure_ascii=False))


if __name__ == '__main__':
    main()
