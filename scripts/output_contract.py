"""Scoped final wording/title checks using the existing sealed inspection cache.

These objective checks supplement the six model checks, never certify semantics.
Only new batches whose frozen policy explicitly selects MODE use this contract.
"""
import re
import batch_tool as b
import invention_contract

MODE = 'wording_title_v18'
PROSE_MODE = 'clean_prose_v19'
BENEFIT_MODE = 'numbered_benefits_v21'
LAYOUT_MODE = 'manual_flow_abstract_v22'
FORMULA_MODE = 'source_math_prose_v23'
SPACING_MODE = 'no_literal_spaces_v26'
KNOWN_YIYI = re.compile(r'一一(?:地)?(?:对应|相应|匹配|映射|配对|关联|绑定|核对|对齐|连接|比较|列出|验证|校验|检查|处理|进行|执行|标记|登记|记录|确认|说明)')


def enabled(manifest):
    return manifest.get('policy',{}).get('output_contract') == MODE


def filename(title):
    """Never silently shorten/sanitize the document's official name."""
    if (not isinstance(title,str) or not title or title != title.strip() or title.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]',title)
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',title)
            or len((title+'.docx').encode('utf-16-le'))//2 > 255):
        raise b.BatchError('Official title cannot be used unchanged as a Windows filename; resolve the title explicitly')
    return title+'.docx'


def check(report, expected_title, findings=(), strict_prose=False, source_hashes=(), strict_benefits=False, strict_layout=False, strict_formulas=False, strict_spacing=False, strict_invention=False):
    contract=report.get('document_contract_evidence',{})
    title=contract.get('title') or {}
    if title.get('text') != expected_title:
        raise b.BatchError('Final DOCX title must exactly match the frozen official project title and delivery filename')
    if not contract.get('description_scope_resolved'):
        raise b.BatchError('Description scope unresolved; cannot certify scoped wording')
    def source_bound(hit,classification,rule_id):
        # Reuse the actual independent review finding, bound to exact source
        # and output positions. Only a verified whole-formula finding uses a paragraph locator.
        if classification=='source_formula' and hit.get('representation') in {'applied_numbering','drawing_bullet'}:
            return False
        return any(isinstance(f,dict) and f.get('rule_id')==rule_id and
                   f.get('classification')==classification and f.get('status')=='closed' and
                   f.get('artifact_sha256')==report['sha256'] and
                   all(f.get(k)==hit.get(k) for k in ('part','paragraph','path','offset','text')) and
                   f.get('source_sha256') in source_hashes and
                   all(isinstance(f.get(k),str) and f[k].strip() for k in ('source_locator','registry_id','reason'))
                   for f in findings)
    def protected_formula(hit):
        return strict_prose and (hit.get('formula_protected_candidate') is True or
                                 source_bound(hit,'source_formula','WORDING-06'))
    violations=[h for h in contract.get('description_word_hits',[]) if not protected_formula(h)]
    for hit in contract.get('global_word_hits',[]):
        if protected_formula(hit) or (strict_prose and hit['text']=='一一' and
                                      source_bound(hit,'protected_technical_name','WORDING-07')):continue
        known=KNOWN_YIYI.match(hit['context'][min(24,hit['offset']):]) if strict_prose else KNOWN_YIYI.search(hit['context'])
        if hit['text']=='从而' or known:
            violations.append(hit)
            continue
        # A sequence spanning two legitimate words (e.g. 统一一套流程) can
        # contain the same characters. It needs the actual independent review
        # finding, tied to the current text position; it is not a replacement.
        resolved=any(isinstance(f,dict) and f.get('rule_id')=='WORDING-04' and
                     f.get('classification')=='not_modifier' and f.get('status')=='closed' and
                     f.get('paragraph')==hit['paragraph'] and f.get('offset')==hit['offset'] and
                     f.get('part','word/document.xml')==hit.get('part','word/document.xml') and
                     f.get('text')==hit['text'] and isinstance(f.get('reason'),str) and f['reason'].strip()
                     for f in findings)
        if not resolved: violations.append(hit)
    if violations:
        where=', '.join(f'{h.get("part","word/document.xml")} P{h["paragraph"]:04}:{h["text"]}' for h in violations[:8])
        raise b.BatchError('Final wording contract not satisfied: '+where)
    if strict_prose:
        punctuation=report.get('punctuation_evidence')
        if (not isinstance(punctuation,dict) or punctuation.get('schema_version')!=2 or
                punctuation.get('literal_scan_complete') is not True or
                any(not isinstance(punctuation.get(k),list) for k in
                    ('hits','review_candidates','formula_protected_candidates','preferred_wording_hits'))):
            raise b.BatchError('Final quotation/bracket inspection is missing or incomplete')
        unprotected=[h for h in punctuation['hits'] if not source_bound(h,'source_formula','WORDING-06')]
        if unprotected:
            where=', '.join(f'{h["part"]} {h["path"]}:{h["codepoint"]}' for h in unprotected[:6])
            raise b.BatchError('Final quotation/bracket contract not satisfied: '+where)
        wording=[h for h in punctuation['preferred_wording_hits']
                 if not (source_bound(h,'protected_technical_name','WORDING-07') or
                         source_bound(h,'source_formula','WORDING-06'))]
        if wording:
            where=', '.join(f'{h["part"]} {h["path"]}:{h["text"]} -> {h["replacement"]}' for h in wording[:6])
            raise b.BatchError('Final preferred wording contract not satisfied: '+where)
    if strict_benefits:
        benefits=report.get('benefit_numbering_evidence')
        if (not isinstance(benefits,dict) or benefits.get('schema_version')!=1 or
                benefits.get('scope_resolved') is not True or
                not isinstance(benefits.get('items'),list) or not isinstance(benefits.get('issues'),list)):
            raise b.BatchError('Final benefit numbering contract evidence missing or unresolved')
        # Text/image/OLE equations and external equation labels need a complete,
        # source-bound independent finding. It protects only this whole formula
        # paragraph, never ordinary effect prose containing an inline equation.
        protected={h.get('paragraph') for h in benefits['items']+benefits['issues']
                   if source_bound(h,'source_formula_paragraph','STYLE-07')}
        items=[h for h in benefits['items'] if h.get('paragraph') not in protected]
        ordinary_codes={'missing_or_noncanonical_effect_number','nonconsecutive_effect_number'}
        issues=[h for h in benefits['issues'] if h.get('paragraph') not in protected
                and h.get('code') not in ordinary_codes]
        numbers=[re.fullmatch(r'([1-9][0-9]*)\.([^\s].*)',h.get('text',''),re.S) for h in items]
        if (issues or not items or any(n is None for n in numbers) or
                [int(n[1]) for n in numbers]!=list(range(1,len(items)+1))):
            raise b.BatchError('Final benefit numbering contract not satisfied; require consecutive 1.,2.,... effect paragraphs')
    if strict_layout:
        flow=report.get('page_flow_evidence')
        if (not isinstance(flow,dict) or flow.get('schema_version')!=1 or
                flow.get('extraction_complete') is not True or flow.get('artifact_sha256')!=report['sha256'] or
                any(not isinstance(flow.get(k),list) for k in
                    ('paragraphs','ordinary_violations','abstract_labels','final_captions','empty_candidates','review_candidates','sections'))):
            raise b.BatchError('Final page flow inspection is missing, incomplete or bound to another artifact')
        if flow['ordinary_violations']:
            where=', '.join(f'{h["part"]} {h["path"]}:{h["property"]}' for h in flow['ordinary_violations'][:6])
            raise b.BatchError('Final page flow contract not satisfied: '+where)
        if flow['abstract_labels']:
            where=', '.join(f'{h["part"]} {h["path"]}' for h in flow['abstract_labels'][:6])
            raise b.BatchError('Final abstract figure must contain its image without extra caption or label: '+where)
        for candidate in flow['review_candidates']:
            resolved=any(isinstance(f,dict) and f.get('rule_id')=='FORMAT-03' and
                         f.get('classification')=='page_flow_reviewed' and f.get('status')=='closed' and
                         f.get('artifact_sha256')==report['sha256'] and
                         all(f.get(k)==candidate.get(k) for k in ('part','paragraph','path')) and
                         isinstance(f.get('reason'),str) and f['reason'].strip()
                         for f in findings)
            if not resolved:
                raise b.BatchError('Final page flow needs a current artifact and located review finding: '+
                                   candidate['part']+' '+candidate['path']+' '+candidate['reason'])
    if strict_formulas:
        formula=report.get('formula_prose_evidence')
        fields=('explanation_lead_hits','standalone_formula_semicolon_hits','bare_symbol_omml_candidates','plain_label_omml_candidates',
                'inline_numeric_omml_candidates','review_candidates','standalone_formulas','group_separator_hits')
        if (not isinstance(formula,dict) or formula.get('schema_version')!=1 or
                formula.get('extraction_complete') is not True or formula.get('complete') is not True or
                formula.get('artifact_sha256')!=report['sha256'] or
                any(not isinstance(formula.get(k),list) for k in fields)):
            raise b.BatchError('Final formula/prose inspection is missing, incomplete or bound to another artifact')
        # Formatting errors are not waivable as source formulas. A source-bound
        # exception applies only to this exact bare/arithmetical math object.
        for key,label in [('explanation_lead_hits','explanation lead must use 其中'),
                          ('standalone_formula_semicolon_hits','standalone formula needs a terminal semicolon'),
                          ('group_separator_hits','complete equation group needs exactly one comma per nonfinal row and one final semicolon')]:
            if formula[key]:
                where=', '.join(f'{h["part"]} {h["path"]}' for h in formula[key][:6])
                raise b.BatchError('Final formula/prose contract not satisfied: '+label+': '+where)
        def source_math_identity(hit):
            return any(isinstance(f,dict) and f.get('rule_id')=='FORMULA-05' and
                       f.get('classification')=='source_math_identity' and f.get('status')=='closed' and
                       f.get('artifact_sha256')==report['sha256'] and
                       isinstance(f.get('source_sha256'),str) and re.fullmatch(r'[0-9a-fA-F]{64}',f['source_sha256']) and
                       f['source_sha256'] in source_hashes and
                       all(f.get(k)==hit.get(k) for k in ('part','paragraph')) and
                       all(k not in hit or (type(f.get(k)) is int and f[k]==hit[k])
                           for k in ('offset','end_offset')) and
                       ('offset' not in hit or f.get('format_disposition') in ('not_formula','format_already_correct')) and
                       ('offset' not in hit or f.get('offset_projection')==hit.get('offset_projection')) and
                       ((isinstance(f.get('path'),str) and f['path'].strip() and f['path']==hit.get('path')) or
                        (isinstance(f.get('object_id'),str) and f['object_id'].strip() and
                         f['object_id']==hit.get('object_id'))) and
                       all(isinstance(f.get(k),str) and f[k].strip() for k in ('registry_id','source_locator','reason'))
                       for f in findings)
        unresolved=[h for key in ('bare_symbol_omml_candidates','plain_label_omml_candidates','inline_numeric_omml_candidates')
                    for h in formula[key] if not source_math_identity(h)]
        if unresolved:
            where=', '.join(f'{h["part"]} {h["path"]}:{h["text"]}' for h in unresolved[:6])
            raise b.BatchError('Final formula/prose contract needs plain text or a located source math identity finding: '+where)
        unresolved_reviews=[h for h in formula['review_candidates'] if not source_math_identity(h)]
        if unresolved_reviews:
            where=', '.join(f'{h["part"]} {h["path"]}:{h["reason"]}' for h in unresolved_reviews[:6])
            raise b.BatchError('Final formula/prose review needs a current source-bound located finding: '+where)
    if strict_spacing:
        spacing=report.get('content_spacing_evidence')
        digest=report.get('sha256')
        if (not isinstance(spacing,dict) or type(spacing.get('schema_version')) is not int or
                spacing.get('schema_version')!=1 or spacing.get('complete') is not True or
                spacing.get('extraction_complete') is not True or
                not isinstance(digest,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',digest) or
                spacing.get('artifact_sha256')!=digest or not isinstance(spacing.get('hits'),list) or
                not isinstance(spacing.get('scope'),str) or not spacing['scope'].strip()):
            raise b.BatchError('Final content spacing inspection is missing, incomplete or bound to another artifact')
        # A source_formula finding does not waive a literal-space violation.
        # Evidence bytes are sealed by verify_prepared; no second DOCX parse.
        if spacing['hits']:
            where=', '.join(f'{h.get("part","?")} {h.get("path","?")} offset {h.get("offset","?")}:{h.get("codepoint","?")}'
                            if isinstance(h,dict) else 'malformed spacing hit' for h in spacing['hits'][:6])
            raise b.BatchError('Final content spacing contract not satisfied: '+where)
    if strict_invention:
        try:invention_contract.validate_evidence(report,findings)
        except ValueError as exc:raise b.BatchError(str(exc)) from exc
    result={'mode':MODE,'artifact_sha256':report['sha256'],'document_title':expected_title,
            'delivery_filename':filename(expected_title),'title_paragraph':title['paragraph'],
            'scope':'Objective title, located wording and enabled numbering/page-flow checks only; original six reviews and final-page acceptance remain required'}
    if strict_prose:result['prose_contract']=PROSE_MODE
    if strict_benefits:result['benefit_contract']=BENEFIT_MODE
    if strict_layout:result['layout_contract']=LAYOUT_MODE
    if strict_formulas:
        result['formula_contract']=FORMULA_MODE
        result['scope']='Objective title, located wording and enabled numbering/page-flow/formula-prose checks only; original six reviews and final-page acceptance remain required'
    if strict_spacing:
        result['spacing_contract']=SPACING_MODE
        result['scope']='Objective title, located wording and enabled numbering/page-flow/formula-prose/literal-spacing checks only; original six reviews and final-page acceptance remain required'
    if strict_invention:result['invention_contract']=invention_contract.MODE
    return result


def verify(m,case,attempt,receipt):
    if not enabled(m): return None
    import prepare_handoff as handoff
    # Hash-bound existing evidence; no second ZIP parse and no new model call.
    try:
        verified = handoff.verify_prepared(m,case,attempt)
    except (ValueError, OSError, KeyError) as exc:
        raise b.BatchError('Final wording/title evidence is invalid: '+str(exc)) from exc
    if not verified or not verified['handoff']['input_inspection']:
        raise b.BatchError('Final wording/title inspection is missing')
    # Reuse the exact JSON object just parsed and verified in this invocation.
    # No persisted pass cache: all paths, bytes and semantic checks still run.
    report=verified['input_report']
    if report.get('sha256') != attempt['input_artifact_sha256']:
        raise b.BatchError('Final wording/title report refers to another artifact')
    return check(report,case['title'],receipt.get('findings',[]),
                 m['policy'].get('prose_contract')==PROSE_MODE, [s['sha256'] for s in case.get('sources',[])],
                 m['policy'].get('benefit_contract')==BENEFIT_MODE,
                 m['policy'].get('layout_contract')==LAYOUT_MODE,
                 m['policy'].get('formula_contract')==FORMULA_MODE,
                 m['policy'].get('spacing_contract')==SPACING_MODE,
                 m['policy'].get('invention_contract')==invention_contract.MODE)
