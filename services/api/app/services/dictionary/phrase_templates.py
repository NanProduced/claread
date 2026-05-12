"""
规范化模板工具，用于识别短语中的 'sb', 'sth', 'sb's' 槽位，并生成 canonical template。
"""
import re
from typing import Literal

def canonicalize_dictionary_phrase(text: str) -> str:
    """
    对词典中导入的带有各种占位符的短语进行纯净化替换，生成 canonical template。
    跟 backfill 脚本中的逻辑保持一致。
    """
    res = text
    res = re.sub(r"\b(somebody's|someone's|one's)\b", "sb's", res, flags=re.IGNORECASE)
    res = re.sub(r"\bsb\.'s\b", "sb's", res, flags=re.IGNORECASE)
    res = re.sub(r'\bsb\.(?=\s|$)', 'sb', res, flags=re.IGNORECASE)
    res = re.sub(r'\b(somebody|someone)\b', 'sb', res, flags=re.IGNORECASE)
    res = re.sub(r'\bsth\.(?=\s|$)', 'sth', res, flags=re.IGNORECASE)
    res = re.sub(r'\bsomething\b', 'sth', res, flags=re.IGNORECASE)
    res = re.sub(r'\s+', ' ', res).strip()
    return res

def classify_slot(token_or_span) -> Literal["sb", "sth", "sb's", None]:
    """
    根据 spaCy 的 Token 或 Span 判断它属于哪种槽位。
    """
    root = token_or_span.root if hasattr(token_or_span, "root") else token_or_span

    thing_possessives = {"its", "their"}
    if root.dep_ == "poss" and root.lemma_.lower() in thing_possessives:
        return "sth"

    if root.dep_ == "poss" or root.tag_ == "PRP$":
        return "sb's"

    person_pronouns = {"i", "me", "you", "he", "him", "she", "her", "we", "us", "they", "them"}
    if root.lemma_.lower() in person_pronouns or root.ent_type_ == "PERSON":
        return "sb"

    thing_pronouns = {"it", "this", "that", "these", "those", "something", "anything", "everything"}
    if root.lemma_.lower() in thing_pronouns:
        return "sth"

    if root.pos_ in ("NOUN", "PROPN", "PRON"):
        return "sth"

    return None

def canonicalize_sentence_span(span, target_token_indices: set[int] = None) -> str:
    """
    将 spaCy Span 转换为 template 字符串。
    将识别出的名词短语或代词替换为 sb / sth。
    target_token_indices 是查询锚点的 index 集合，这些词不应该被替换为槽位。
    """
    if target_token_indices is None:
        target_token_indices = set()

    words = []
    if len(span) == 1:
        return span.text.lower()

    doc = span.doc
    noun_chunks = list(doc.noun_chunks)

    i = span.start
    while i < span.end:
        token = doc[i]

        # 寻找包含该 token 的 chunk
        chunk = next((c for c in noun_chunks if c.start <= i < c.end), None)

        # 如果有 chunk，并且查询词不在 chunk 中，并且 chunk 不是单个词且刚好是查询词
        if chunk and chunk.start >= span.start and chunk.end <= span.end:
            chunk_indices = set(range(chunk.start, chunk.end))
            if not chunk_indices.intersection(target_token_indices):
                poss_tokens = [t for t in chunk if t.dep_ == "poss" or t.tag_ == "PRP$"]
                if poss_tokens:
                    poss_slot = classify_slot(poss_tokens[0])
                    if poss_slot == "sb's":
                        for t in chunk:
                            if t.dep_ == "case":
                                continue
                            if t.dep_ == "poss" or t.tag_ == "PRP$":
                                words.append("sb's")
                            else:
                                words.append(t.lemma_.lower())
                        i = chunk.end
                        continue
                slot = classify_slot(chunk)
                if slot:
                    words.append(slot)
                else:
                    words.append(chunk.lemma_.lower())
                i = chunk.end
                continue

        # 否则按词处理
        if i in target_token_indices:
            words.append(token.lemma_.lower())
        else:
            slot = classify_slot(token)
            if slot and token.pos_ not in ("VERB", "ADP", "PART"):
                words.append(slot)
            else:
                words.append(token.lemma_.lower())
        i += 1

    return " ".join(words)
