from __future__ import annotations

import sys
import textwrap
import types
from pathlib import Path

import pytest

try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:
    sys.modules["asyncpg"] = types.SimpleNamespace(Connection=object)

from app.services.dictionary.service import DictionaryService
from scripts.import_tecd3 import (
    build_lookup_forms,
    load_txt_records,
    normalize_query,
    parse_disambiguation_html,
    parse_entry_html,
)


ENTRY_HTML = """
<html><body>
  <div class="eDiv" id="actualize-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">ac·tu·al·ize</span>
          <span class="hwFollowSpan">
            <span class="prLine"><pr soundfile="root">ˈæktʃʊəlaɪz</pr></span>
          </span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="subse1">
          <div class="sgPosDiv"><span class="posg"><span class="pos">TRANSITIVE VERB 及物动词</span></span></div>
          <ol class="se2g">
            <li class="se2"><span class="df">实行(计划等),把…化为行动</span></li>
            <li class="se2"><span class="df">发挥出…的潜力</span></li>
          </ol>
        </div>
        <div class="subse1">
          <div class="sgPosDiv"><span class="posg"><span class="pos">INTRANSITIVE VERB 不及物动词</span></span></div>
          <ol class="se2g">
            <li class="se2"><span class="df">成为现实</span></li>
            <li class="se2"><span class="df">发挥出潜力</span></li>
          </ol>
        </div>
      </div>
    </div>
    <div class="phrase">
      <span class="hwSpan">actualize a plan</span>
      <span class="df">实行计划</span>
    </div>
  </div>
</body></html>
"""


HOMOGRAPH_HTML = """
<html><body>
  <div class="eDiv" id="anth-two">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw homograph="2">anth-</hw><sup>2</sup></span>
          <span class="hwFollowSpan"><span class="prLine"><pr soundfile="anth">ænθ</pr></span></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">PREFIX 前缀</span></span></div>
        <ol class="se2g se2gOne">
          <li class="se2">
            <span class="corrSe2FirstLine"><xrg>=<a class="xr" href="entry://anti-">anti-</a></xrg><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""


FRAGMENT_HTML = """
<html><body>
  <div class="mdict-fragment-header">
    <div class="mdict-fragment-title">each and all</div>
    <div class="mdict-fragment-parent">主词条：<a class="mdict-parent-link" href="entry://each">each</a></div>
  </div>
  <div class="mdict-fragment-body">
    <div class="phrasediv">
      <ol class="se2g se2gOne">
        <li class="se2">
          <span class="corrSe2FirstLine"><span class="df">人人；各个；全部</span><br/></span>
        </li>
      </ol>
    </div>
  </div>
</body></html>
"""


WBR_HEADWORD_HTML = """
<html><body>
  <div class="eDiv" id="rose-water">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw>rose <wbr/>water</hw></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <ol class="se2g">
          <li class="se2"><span class="df">玫瑰水</span></li>
          <li class="se2"><span class="df">温和的话语</span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""


MIXED_SECTION_HTML = """
<html><body>
  <div class="eDiv" id="round-one">
    <div class="hg">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw homograph="1">round</hw><sup>1</sup></span>
          <span class="hwFollowSpan"><span class="prLine"><pr soundfile="round">raʊnd</pr></span></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">ADJECTIVE 形容词</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">圆(形)的</span></li>
        </ol>
      </div>
      <div class="se1">
        <div class="subse1">
          <div class="sgPosDiv"><span class="posg"><span class="pos">TRANSITIVE VERB 及物动词</span></span></div>
          <ol class="se2g">
            <li class="se2"><span class="df">使成圆形</span></li>
          </ol>
        </div>
        <div class="subse1">
          <div class="sgPosDiv"><span class="posg"><span class="pos">INTRANSITIVE VERB 不及物动词</span></span></div>
          <ol class="se2g">
            <li class="se2"><span class="df">变圆</span></li>
          </ol>
        </div>
      </div>
    </div>
    <div class="phrase">
      <div class="phrasediv">
        <div class="lDiv"><span class="l">all round</span><br/></div>
        <ol class="se2g se2gOne">
          <li class="se2"><span class="corrSe2FirstLine"><span class="df">见 all</span><br/></span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""


NLP_ENTRY_HTML = """
<html><body>
  <div class="eDiv" id="crew-one">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">crew</span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">NOUN 名词</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">全体船员</span></li>
        </ol>
      </div>
    </div>
    <div class="nlp" style="display: none;"><pl>crews</pl><prp>crewing</prp><past>crewed</past><pp>crewed</pp></div>
    <div class="nlp" style="display: none;">crew up</div>
  </div>
</body></html>
"""

ABBREVIATION_ENTRY_HTML = """
<html><body>
  <div class="eDiv" id="aar-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">AAR</span>
        </div>
      </div>
      <div class="mdict-entry-nav">
        <ul class="mdict-entry-nav-list">
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-1">abbr.</a></li>
        </ul>
      </div>
    </div>
    <div class="sg">
      <a class="mdict-pos-anchor" id="mdict-pos-1" name="mdict-pos-1"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">ABBREVIATION 缩略词</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">air-to-air refueling 空中加油</span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

DEMONSTRATIVE_PRONOUN_HTML = """
<html><body>
  <div class="eDiv" id="that-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">that</span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">DEMONSTRATIVE PRONOUN 指示代词</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">那，那个</span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

COMBINING_FORM_NAV_HTML = """
<html><body>
  <div class="eDiv" id="dynamo-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">dy·na·mo-</span>
        </div>
      </div>
      <div class="mdict-entry-nav">
        <ul class="mdict-entry-nav-list">
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-1">comb.</a></li>
        </ul>
      </div>
    </div>
    <div class="sg">
      <a class="mdict-pos-anchor" id="mdict-pos-1" name="mdict-pos-1"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">COMBINING FORM 组合语素</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">表示“力”“动力”</span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

NUMBERED_NAV_POS_HTML = """
<html><body>
  <div class="eDiv" id="suffix-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">-wise</span>
        </div>
      </div>
      <div class="mdict-entry-nav">
        <ul class="mdict-entry-nav-list">
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-1">suf. 2</a></li>
        </ul>
      </div>
    </div>
    <div class="sg">
      <a class="mdict-pos-anchor" id="mdict-pos-1" name="mdict-pos-1"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">SUFFIX 后缀</span></span></div>
        <ol class="se2g">
          <li class="se2"><span class="df">表示“方向”“方式”</span></li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

A1_MULTI_HEADWORD_HTML = """
<html><body>
  <div class="eDiv" id="a1-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw>A1</hw><span class="hwFollowSepa"></span><wbr/></span>
          <span class="hwSpan"><hw>A-1</hw></span>
        </div>
      </div>
      <div class="mdict-entry-nav">
        <ul class="mdict-entry-nav-list">
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-1">adj.</a></li>
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-2">n.</a></li>
        </ul>
      </div>
    </div>
    <div class="sg">
      <a class="mdict-pos-anchor" id="mdict-pos-1" name="mdict-pos-1"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">ADJECTIVE 形容词</span></span></div>
        <ol class="se2g">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">(船)船体及设备均属第一等的</span><br/></span>
            <ul class="egBlock"></ul>
          </li>
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">第一流的,极好的</span><br/></span>
            <ul class="egBlock">
              <li class="eg"><span class="ex">A1 tea</span><span class="tr">上品茶叶</span></li>
              <li class="eg"><span class="ex">an A1 physicist</span><span class="tr">物理学大牛</span></li>
              <li class="eg"><span class="ex">The meals there are A1.</span><span class="tr">那里的伙食顶呱呱。</span></li>
              <li class="eg"><span class="ex">feel A1</span><span class="tr">自我感觉极好</span></li>
            </ul>
          </li>
        </ol>
      </div>
      <a class="mdict-pos-anchor" id="mdict-pos-2" name="mdict-pos-2"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">NOUN 名词</span></span></div>
        <ol class="se2g se2gOne">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">(英国《劳氏船名录》上的)一级船</span><br/></span>
            <ul class="egBlock"></ul>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

ABBREVIATION_LABEL_HTML = """
<html><body>
  <div class="eDiv" id="aar-label-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw>AAR</hw></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">ABBREVIATION 缩略词</span></span></div>
        <ol class="se2g">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="l">against all risks</span><span class="spaceSepa"></span><span class="df">综合险,一切(保)险</span><br/></span>
          </li>
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="l">Association of American Railroads</span><span class="spaceSepa"></span><span class="df">美国铁路协会</span><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

REDIRECT_ONLY_HTML = """
<html><body>
  <div class="eDiv" id="aar-redirect-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw>Aar</hw></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="se1TopOffset"></div>
        <ol class="se2g se2gOne">
          <li class="se2">
            <span class="corrSe2FirstLine"><xrg>=<a class="xr" href="entry://Aare"><span class="spaceSepa"></span>Aare</a><span class="spaceSepa"></span></xrg><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

AARON_INLINE_FORMATTING_HTML = """
<html><body>
  <div class="eDiv" id="aaron-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">Aar·on</span>
          <span class="hwFollowSpan"><span class="prLine"><pr>ˈeər<i>ə</i>n, ˈɑːrən</pr></span></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <a class="sgN"></a>
        <div class="sgPosDiv"><span class="posg"><span class="pos">NOUN 名词</span></span></div>
        <ol class="se2g">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">艾伦(<i>m.</i>)</span><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

LETTER_ENTRY_WITH_IMPLICIT_NOUN_HTML = """
<html><body>
  <div class="eDiv" id="letter-a-entry">
    <div class="hg">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan"><hw>A</hw><span class="hwFollowSepa"></span><wbr/></span>
          <span class="hwSpan"><hw>a</hw></span>
          <span class="hwFollowSpan"><span class="prLine"><pr>eɪ</pr></span></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <a class="sgN"></a>
        <div class="sgPosDiv"></div>
        <ol class="se2g">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">英语的第一个字母</span><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

INFLECTION_REDIRECT_HTML = """
<html><body>
  <div class="eDiv" id="abaci-entry">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">ab·a·ci</span>
          <span class="hwFollowSpan"><span class="prLine"><pr>ˈæbəsaɪ</pr></span></span>
        </div>
      </div>
    </div>
    <div class="sg">
      <div class="se1">
        <div class="sgPosDiv"></div>
        <ol class="se2g se2gOne">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df"><xrg><a class="xr" href="entry://abacus">abacus</a></xrg>的复数</span><br/></span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""

DYNAMO_MULTI_EXAMPLE_HTML = """
<html><body>
  <div class="eDiv" id="dynamo-multi">
    <div class="hg nopos">
      <div class="hgContent">
        <div class="hwgDiv">
          <span class="hwSpan">dy·na·mo-</span>
        </div>
      </div>
      <div class="mdict-entry-nav">
        <ul class="mdict-entry-nav-list">
          <li class="mdict-entry-nav-item"><a class="mdict-entry-nav-link" href="#mdict-pos-1">comb.</a></li>
        </ul>
      </div>
    </div>
    <div class="sg">
      <a class="mdict-pos-anchor" id="mdict-pos-1" name="mdict-pos-1"></a>
      <div class="se1">
        <div class="sgPosDiv"><span class="posg"><span class="pos">COMBINING FORM 组合语素</span></span></div>
        <ol class="se2g se2gOne">
          <li class="se2">
            <span class="corrSe2FirstLine"><span class="df">表示“力”“动力”</span><br/></span>
            <ul class="egBlock">
              <li class="eg">
                <exw>
                  <a class="ex" href="entry://dynamoelectric"><i>dynamo</i>electric</a>
                  <span class="commaSepa"></span>
                  <a class="ex" href="entry://dynamometer"><i>dynamo</i>meter</a>
                </exw>
              </li>
            </ul>
          </li>
        </ol>
      </div>
    </div>
  </div>
</body></html>
"""


DISAMB_HTML = """
<html><body>
  <div class="mdict-disamb">
    <div class="mdict-disamb-title">anti</div>
    <div class="mdict-disamb-tip">该检索词命中多个词条，请选择具体入口。</div>
  </div>
  <ul class="mdict-disamb-list">
    <li class="mdict-disamb-item">
      <a class="mdict-target-link" href="entry://anti · n.,a.,prep.">anti</a>
      <div class="mdict-target-meta"><span class="pos">n.,a.,prep.</span></div>
      <div class="mdict-target-preview">1. 反对者,反对分子,持反对论者</div>
    </li>
    <li class="mdict-disamb-item">
      <a class="mdict-target-link" href="entry://anti- · pref.">anti-</a>
      <div class="mdict-target-meta"><span class="pos">pref.</span></div>
      <div class="mdict-target-preview">1. 表示“反”“抗”“阻”</div>
    </li>
  </ul>
</body></html>
"""


def _write_txt(path: Path, contents: str) -> None:
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


def test_load_txt_records_reads_html_and_redirects(tmp_path: Path) -> None:
    mdx_file = tmp_path / "sample.mdx.a.txt"
    _write_txt(
        mdx_file,
        """
        anti
        <html><body><div class="mdict-disamb"></div></body></html>
        </>
        anti-
        @@@LINK=anti · n.,a.,prep.
        </>
        """,
    )

    records = load_txt_records(tmp_path)

    assert records["anti"].kind == "html"
    assert records["anti-"].kind == "redirect"
    assert records["anti-"].value == "anti · n.,a.,prep."


def test_load_txt_records_rejects_duplicate_keys(tmp_path: Path) -> None:
    mdx_file = tmp_path / "sample.mdx.a.txt"
    _write_txt(
        mdx_file,
        """
        anti
        <html><body><div class="eDiv"></div></body></html>
        </>
        anti
        @@@LINK=anti · n.,a.,prep.
        </>
        """,
    )

    with pytest.raises(ValueError, match="Duplicate mdict key"):
        load_txt_records(tmp_path)


def test_parse_entry_html_extracts_entry_payload() -> None:
    parsed = parse_entry_html("actualize", ENTRY_HTML)

    assert parsed is not None
    assert parsed.source_entry_key == "actualize"
    assert parsed.entry_kind == "entry"
    assert parsed.display_headword == "actualize"
    assert parsed.base_headword == "actualize"
    assert parsed.homograph_no is None
    assert parsed.phonetic == "ˈæktʃʊəlaɪz"
    assert parsed.meanings_json[0]["part_of_speech"] == "vt."
    assert parsed.meanings_json[1]["part_of_speech"] == "vi."
    assert parsed.phrases_json[0]["phrase"] == "actualize a plan"
    assert parsed.sections_json


def test_parse_entry_html_handles_homograph_labels_and_xrg_meanings() -> None:
    parsed = parse_entry_html("anth- · pref.", HOMOGRAPH_HTML)

    assert parsed is not None
    assert parsed.display_headword == "anth-²"
    assert parsed.base_headword == "anth-"
    assert parsed.homograph_no == 2
    assert parsed.phonetic == "ænθ"
    assert parsed.meanings_json[0]["definitions"][0]["meaning"] == "=anti-"


def test_parse_entry_html_handles_fragment_entries() -> None:
    parsed = parse_entry_html("each and all", FRAGMENT_HTML)

    assert parsed is not None
    assert parsed.entry_kind == "fragment"
    assert parsed.display_headword == "each and all"
    assert parsed.base_headword == "each and all"
    assert parsed.meanings_json[0]["definitions"][0]["meaning"] == "人人；各个；全部"


FRAGMENT_DERIVATIVE_HTML = """
<html><body>
  <div class="mdict-fragment-header">
    <div class="mdict-fragment-title">chronically</div>
    <div class="mdict-fragment-parent">主词条：<a class="mdict-parent-link" href="entry://chronic">chronic</a></div>
  </div>
  <div class="mdict-fragment-body">
    <div class="derivativeDiv">
      <span class="l">chron·i·cal·ly</span><span class="pr"></span><span class="posg"><span class="pos">ADVERB 副词</span></span>
    </div>
  </div>
</body></html>
"""


def test_parse_entry_html_fragment_derivative_gets_parent_redirect() -> None:
    parsed = parse_entry_html("chronically", FRAGMENT_DERIVATIVE_HTML)

    assert parsed is not None
    assert parsed.entry_kind == "fragment"
    assert parsed.display_headword == "chronically"
    assert parsed.meanings_json == []
    assert parsed.redirect_target_entry_key == "chronic"


def test_parse_entry_html_fragment_with_meanings_keeps_no_redirect() -> None:
    parsed = parse_entry_html("each and all", FRAGMENT_HTML)

    assert parsed is not None
    assert parsed.entry_kind == "fragment"
    assert parsed.meanings_json != []
    assert parsed.redirect_target_entry_key is None


def test_parse_entry_html_preserves_wbr_spacing_in_headwords() -> None:
    parsed = parse_entry_html("rose water · 1. 玫瑰水", WBR_HEADWORD_HTML)

    assert parsed is not None
    assert parsed.display_headword == "rose water"
    assert parsed.base_headword == "rose water"


def test_parse_entry_html_preserves_superscript_display_and_keeps_all_pos_sections() -> None:
    parsed = parse_entry_html("round¹", MIXED_SECTION_HTML)

    assert parsed is not None
    assert parsed.display_headword == "round¹"
    assert parsed.base_headword == "round"
    assert parsed.homograph_no == 1
    assert [group["part_of_speech"] for group in parsed.meanings_json] == [
        "adj.",
        "vt.",
        "vi.",
    ]
    assert parsed.phrases_json[0]["phrase"] == "all round"
    assert parsed.phrases_json[0]["meaning"] == "见 all"


def test_parse_entry_html_extracts_hidden_nlp_lookup_forms() -> None:
    parsed = parse_entry_html("crew · n.", NLP_ENTRY_HTML)

    assert parsed is not None
    assert parsed.nlp_forms == ["crews", "crewing", "crewed", "crew up"]
    assert parsed.meanings_json[0]["part_of_speech"] == "n."


def test_parse_entry_html_normalizes_abbreviation_and_nav_pos_labels() -> None:
    parsed = parse_entry_html("AAR · abbr.", ABBREVIATION_ENTRY_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "abbr."


def test_parse_entry_html_normalizes_demonstrative_pronoun_to_pron() -> None:
    parsed = parse_entry_html("that", DEMONSTRATIVE_PRONOUN_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "pron."


def test_parse_entry_html_normalizes_combining_form_nav_alias() -> None:
    parsed = parse_entry_html("dynamo- · comb. form", COMBINING_FORM_NAV_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "comb. form"


def test_parse_entry_html_strips_numeric_suffix_from_nav_pos_label() -> None:
    parsed = parse_entry_html("-wise · suf.", NUMBERED_NAV_POS_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "suf."


def test_parse_entry_html_preserves_multi_example_blocks() -> None:
    parsed = parse_entry_html("dynamo- · comb. form", DYNAMO_MULTI_EXAMPLE_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "comb. form"
    assert parsed.meanings_json[0]["definitions"][0]["meaning"] == '表示"力" "动力"'
    assert parsed.meanings_json[0]["definitions"][0]["example"] == "dynamo electric；dynamo meter"
    assert parsed.examples_json[0]["example"] == "dynamo electric；dynamo meter"


def test_parse_entry_html_keeps_headword_variants_and_pairs_examples_per_li() -> None:
    parsed = parse_entry_html("A1, A-1", A1_MULTI_HEADWORD_HTML)

    assert parsed is not None
    assert parsed.display_headword == "A1"
    assert parsed.base_headword == "A1"
    assert parsed.headword_variants == ["A1", "A-1"]
    assert parsed.meanings_json[0]["definitions"][0]["example"] is None
    assert parsed.meanings_json[0]["definitions"][1]["example"] == (
        "A1 tea；an A1 physicist；The meals there are A1.；feel A1"
    )
    assert parsed.meanings_json[0]["definitions"][1]["example_translation"] == (
        "上品茶叶；物理学大牛；那里的伙食顶呱呱。；自我感觉极好"
    )
    assert [example["example"] for example in parsed.examples_json] == [
        "A1 tea",
        "an A1 physicist",
        "The meals there are A1.",
        "feel A1",
    ]


def test_parse_entry_html_keeps_abbreviation_expansion_labels() -> None:
    parsed = parse_entry_html("AAR · abbr.", ABBREVIATION_LABEL_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "abbr."
    assert parsed.meanings_json[0]["definitions"][0]["meaning"] == (
        "against all risks 综合险,一切(保)险"
    )
    assert parsed.meanings_json[0]["definitions"][1]["meaning"] == (
        "Association of American Railroads 美国铁路协会"
    )


def test_parse_entry_html_marks_xrg_only_entry_as_redirect() -> None:
    parsed = parse_entry_html("Aar · =Aare", REDIRECT_ONLY_HTML)

    assert parsed is not None
    assert parsed.display_headword == "Aar"
    assert parsed.redirect_target_entry_key == "Aare"


def test_parse_entry_html_removes_inline_spacing_noise() -> None:
    parsed = parse_entry_html("Aaron · n.", AARON_INLINE_FORMATTING_HTML)

    assert parsed is not None
    assert parsed.phonetic == "ˈeərən, ˈɑːrən"
    assert parsed.meanings_json[0]["definitions"][0]["meaning"] == "艾伦(m.)"


def test_parse_entry_html_infers_pos_from_section_marker() -> None:
    parsed = parse_entry_html("A, a", LETTER_ENTRY_WITH_IMPLICIT_NOUN_HTML)

    assert parsed is not None
    assert parsed.headword_variants == ["A", "a"]
    assert parsed.meanings_json[0]["part_of_speech"] == "n."


def test_parse_entry_html_marks_inflection_xrg_entry_as_redirect() -> None:
    parsed = parse_entry_html("abaci · abacus的复数", INFLECTION_REDIRECT_HTML)

    assert parsed is not None
    assert parsed.display_headword == "abaci"
    assert parsed.redirect_target_entry_key == "abacus"


def test_parse_entry_html_infers_noun_for_letter_entries_without_pos_marker() -> None:
    parsed = parse_entry_html("A, a · 1. 英语的第一个字母 2. 字母…", LETTER_ENTRY_WITH_IMPLICIT_NOUN_HTML)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "n."


def test_parse_entry_html_infers_noun_for_symbol_code_entries() -> None:
    parsed = parse_entry_html("A.A. · 表示“(电影)只供14岁以上观众观…", """
    <html><body>
      <div class="eDiv">
        <div class="hg nopos">
          <div class="hgContent">
            <div class="hwgDiv">
              <span class="hwSpan"><hw>A.A.</hw></span>
            </div>
          </div>
        </div>
        <div class="sg">
          <div class="se1">
            <div class="sgPosDiv"></div>
            <ol class="se2g se2gOne">
              <li class="se2">
                <span class="corrSe2FirstLine"><span class="lg">〈<ge>英</ge>〉</span><span class="df">表示“(电影)只供14岁以上观众观看”的级别代号</span><br/></span>
              </li>
            </ol>
          </div>
        </div>
      </div>
    </body></html>
    """)

    assert parsed is not None
    assert parsed.meanings_json[0]["part_of_speech"] == "n."


def test_parse_disambiguation_html_extracts_candidates() -> None:
    parsed = parse_disambiguation_html("anti", DISAMB_HTML)

    assert parsed is not None
    assert parsed.lookup_key == "anti"
    assert parsed.lookup_label == "anti"
    assert parsed.normalized_form == "anti"
    assert len(parsed.candidates) == 2
    assert parsed.candidates[0].target_entry_key == "anti · n.,a.,prep."
    assert parsed.candidates[1].label == "anti-"
    assert parsed.candidates[1].target_pos == "pref."


def test_build_lookup_forms_expands_hyphen_and_apostrophe_variants() -> None:
    forms = build_lookup_forms("world's-best")

    assert "world's-best" in forms
    assert "worlds-best" in forms
    assert "world's best" in forms
    assert "worldsbest" in forms


def test_normalize_query_and_service_normalize_align() -> None:
    service = DictionaryService()

    assert normalize_query(" U.S. ") == "us"
    assert normalize_query("anth- 2") == "anth-2"
    assert service._normalize("“World’s”") == "world's"
    assert service._normalize("(state-owned)") == "state-owned"
