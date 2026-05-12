"""
围绕点击锚点生成候选形式：字面形式、词元形式和模板形式。
"""
from app.services.dictionary.nlp import check_dict_spacy_model, get_dict_nlp, get_dict_matcher
from app.services.dictionary.phrase_templates import canonicalize_sentence_span
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def generate_candidates(query: str, context_sentence: str, occurrence: int | None, doc: Optional["spacy.tokens.Doc"] = None) -> list[str]:
    """
    基于 spaCy 的解析树或 Matcher 模式，生成候选的短语 form 列表。
    """
    import spacy
    forms = []

    if not context_sentence:
        return forms

    try:
        if not check_dict_spacy_model():
            return forms

        if doc is None:
            nlp = get_dict_nlp()
            doc = nlp(context_sentence)

        # 0. Lightweight Matcher Layer
        # 专门覆盖高频或有界模式（如比较级、be there for sb 等）
        matcher = get_dict_matcher()
        if matcher:
            matches = matcher(doc)
            for match_id, start, end in matches:
                span = doc[start:end]
                # 如果查询词或词元在这个 span 内，则添加这个 span 作为候选
                if any(t.text.lower() == query.lower() or t.lemma_.lower() == query.lower() for t in span):
                    # 获取该 span 在当前 doc 下的锚点 tokens
                    # 这里取最匹配 query 的那个 token 作为模板化锚点
                    target_token_indices = {t.i for t in span if t.text.lower() == query.lower() or t.lemma_.lower() == query.lower()}

                    literal = span.text.lower()
                    lemma_form = " ".join([t.lemma_.lower() for t in span])
                    template_form = canonicalize_sentence_span(span, target_token_indices)

                    if literal != query.lower():
                        forms.append(literal)
                    if lemma_form != literal and lemma_form != query.lower():
                        forms.append(lemma_form)
                    if template_form != lemma_form and template_form != literal and template_form != query.lower():
                        forms.append(template_form)

        # 寻找匹配 query 的 token
        target_tokens = [t for t in doc if t.text.lower() == query.lower() or t.lemma_.lower() == query.lower()]

        if not target_tokens:
            return forms

        if occurrence is not None and 1 <= occurrence <= len(target_tokens):
            target = target_tokens[occurrence - 1]
        elif len(target_tokens) >= 1:
            target = target_tokens[0]
        else:
            return forms

        # Helper 添加 form
        def add_span_forms(span):
            literal = span.text.lower()
            lemma_form = " ".join([t.lemma_.lower() for t in span])
            template_form = canonicalize_sentence_span(span, {target.i})

            if literal != query.lower():
                forms.append(literal)
            if lemma_form != literal and lemma_form != query.lower():
                forms.append(lemma_form)
            if template_form != lemma_form and template_form != literal and template_form != query.lower():
                forms.append(template_form)

        def add_form_once(form: str):
            normalized = " ".join(form.lower().split())
            if normalized and normalized != query.lower() and normalized not in forms:
                forms.append(normalized)

        # 1. target 的完整子树
        subtree = list(target.subtree)
        if len(subtree) > 1:
            subtree_set = {t.i for t in subtree}
            subtree.sort(key=lambda x: x.i)
            span = doc[subtree[0].i : subtree[-1].i + 1]
            has_irrelevant = any(t.i not in subtree_set for t in span)
            if has_irrelevant:
                filtered = [t for t in span if t.i in subtree_set]
                literal = " ".join(t.text for t in filtered).lower()
                lemma_form = " ".join(t.lemma_.lower() for t in filtered)
                template_form = canonicalize_sentence_span(span, {target.i})
                if literal != query.lower():
                    forms.append(literal)
                if lemma_form != literal and lemma_form != query.lower():
                    forms.append(lemma_form)
                if template_form != lemma_form and template_form != literal and template_form != query.lower():
                    forms.append(template_form)
            else:
                add_span_forms(span)

        # 2. Anchor Lifter (Verb/Predicate Head Discovery)
        # 目标：从宾语、介词宾语、修饰语等回溯到谓词头，以识别完整短语
        def find_logical_verb_head(token):
            curr = token
            # 限制深度 3 层，避免过度提升
            for _ in range(3):
                if curr.pos_ in ("VERB", "AUX"):
                    return curr
                if not curr.head or curr.head == curr:
                    break

                # 受限追溯关系
                valid_up_deps = {"pobj", "dobj", "advmod", "acomp", "prt", "prep", "dative", "attr", "npadvmod"}

                # 针对所有格 sb's 的特殊追溯逻辑
                # 如果当前词是被所有格修饰的名词 (如 mind 在 one's mind 中)
                # 且点击的是 mind，则允许向上追溯到谓词 (如 make up)
                is_poss_head = any(c.dep_ == "poss" and (
                    c.lemma_.lower() in {"i", "you", "he", "she", "we", "they", "my", "your", "his", "her", "our", "their", "one", "someone", "somebody"} or
                    c.text.lower() in {"sb", "sth"}
                ) for c in curr.children)

                if curr.dep_ in valid_up_deps or is_poss_head:
                    curr = curr.head
                else:
                    break
            return None

        verb_head = find_logical_verb_head(target)

        if verb_head:
            # Bare phrasal fallback: many TECD3 phrase targets are stored as
            # "verb particle" (e.g. "look up", "find out") rather than the
            # longer contextual span ("look up sth in sth").
            if target.dep_ in {"prt", "prep"}:
                add_form_once(f"{verb_head.lemma_.lower()} {target.lemma_.lower()}")
            elif target.dep_ == "advmod":
                add_form_once(f"{verb_head.lemma_.lower()} {target.lemma_.lower()}")
                for child in verb_head.children:
                    if child.dep_ == "prep" and child.i > target.i:
                        add_form_once(f"{verb_head.lemma_.lower()} {target.lemma_.lower()} {child.lemma_.lower()}")

            # Full relaxed phrase
            valid_deps = {"prt", "prep", "dobj", "dative", "advmod", "acomp", "attr"}
            phrase_tokens = [verb_head]
            for child in verb_head.children:
                if child.dep_ in valid_deps:
                    phrase_tokens.append(child)
                    if child.dep_ == "prep":
                        phrase_tokens.extend([c for c in child.children if c.dep_ in ("pobj", "pcomp")])

            if target in phrase_tokens:
                phrase_tokens.sort(key=lambda t: t.i)
                span = doc[phrase_tokens[0].i : phrase_tokens[-1].i + 1]
                add_span_forms(span)

            # Strict core phrase: only include target + prep + dobj + prt
            core_deps = {"prt", "prep", "dobj", "dative", "acomp"}
            strict_tokens = [verb_head]
            if target not in strict_tokens:
                strict_tokens.append(target)

            for child in verb_head.children:
                if child.dep_ in core_deps and child not in strict_tokens:
                    strict_tokens.append(child)
                    if child.dep_ == "prep":
                        strict_tokens.extend([c for c in child.children if c.dep_ in ("pobj", "pcomp")])

            strict_tokens.sort(key=lambda t: t.i)
            if len(strict_tokens) > 1:
                span2 = doc[strict_tokens[0].i : strict_tokens[-1].i + 1]
                add_span_forms(span2)

        # 3. Comparative Structure Extractor (ADJ/ADV + than)
        if target.pos_ in ("ADJ", "ADV"):
            # 寻找 "than" 作为比较结构的标记
            than_token = None
            for child in target.children:
                if child.text.lower() == "than":
                    than_token = child
                    break
            # 也检查 target 的 head 的 children（有时 than 挂在更高层）
            if than_token is None and target.head:
                for child in target.head.children:
                    if child.text.lower() == "than" and abs(child.i - target.i) <= 3:
                        than_token = child
                        break

            if than_token:
                comp_start = min(target.i, than_token.i)
                comp_end = max(target.i, than_token.i)
                comp_span = doc[comp_start : comp_end + 1]
                add_span_forms(comp_span)

        # 4. Anchored N-gram Fallback
        # 当上面所有 parse-based 提取都没产生候选时，
        # 以 target 为锚点生成有限窗口的 n-gram
        if not forms:
            _generate_ngram_fallback(doc, target, query, forms, max_candidates=8)

    except Exception as e:
        logger.warning(f"dict: spaCy candidate generation failed: {e}")

    unique_forms = []
    for f in forms:
        if f not in unique_forms:
            unique_forms.append(f)

    return unique_forms


def _generate_ngram_fallback(doc, target, query: str, forms: list[str], max_candidates: int = 8):
    """
    在 parse-based 提取没有 form 时，以 target 为锚点生成窗口 2-4 的 anchored n-gram。
    只取 lemma 小写形式。
    """
    count = 0
    for window in range(2, 5):  # 2, 3, 4
        # target 在 n-gram 中的不同位置
        for start_offset in range(window):
            start = target.i - start_offset
            end = start + window
            if start < 0 or end > len(doc):
                continue

            tokens = [doc[i] for i in range(start, end)]
            if any(t.is_punct for t in tokens):
                continue

            literal = " ".join(t.text.lower() for t in tokens)
            lemma_form = " ".join(t.lemma_.lower() for t in tokens)
            if literal != query.lower() and literal not in forms:
                forms.append(literal)
                count += 1
                if count >= max_candidates:
                    return
            if lemma_form != literal and lemma_form != query.lower() and lemma_form not in forms:
                forms.append(lemma_form)
                count += 1
                if count >= max_candidates:
                    return
