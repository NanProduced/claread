"""BBC article cd6684a0 regression fixture for grammar-window window verification.

Source: BBC News article that originally produced 37 reading units + 46 anchor_segments
with 34 grammar_note + 26 sentence_analysis = 60 AI annotations.

Note: 实际 BBC 文章内容受版权保护。本 fixture 构造同等长度（6064 chars）
和同等结构特征（heading + 多 paragraph + blockquote + 多 short unit）的英文新闻样本，
用于验证 grammar-window window 切分逻辑（unit 不可拆、heading 触发 hard boundary、
isolation block 独立成 window），而非验证与 cd6684a0 的逐字一致性。

如需运行真实 BBC 端到端 regression，需要从 stable_reading_documents 表导出
cd6684a0 的实际 source_text 替换 BBC_ARTICLE_TEXT。
"""
from uuid import UUID

BBC_RECORD_ID = UUID("cd6684a0-c31b-4474-ba8e-ed0039a6c4ee")
BBC_SOURCE_LANGUAGE = "en"
BBC_ARTICLE_TITLE = "Global Tech Companies Announce Quarterly Results"

# 构造 6064 chars 的 BBC 风格英文新闻样本
BBC_ARTICLE_TEXT = """Global Tech Companies Announce Quarterly Results

Major technology companies released their quarterly earnings this week, revealing mixed results across the sector. The announcements come amid growing concerns about global economic conditions and their impact on consumer spending.

Industry analysts have been closely watching the performance of leading technology firms, which are often seen as a bellwether for the broader market. The latest reports show that while some companies exceeded expectations, others fell short of analyst projections.

Revenue Growth Patterns

Revenue at the largest technology companies grew by an average of 8.2% year over year, according to preliminary analysis. This represents a slowdown from the previous quarter's 12.4% growth rate, but remains above the historical average for the sector.

Cloud computing divisions continued to be a bright spot, with several companies reporting growth rates exceeding 25% in their enterprise cloud businesses. "The shift to cloud infrastructure continues to accelerate," said one industry analyst. "We are seeing both small and large enterprises moving more of their workloads to the cloud."

However, hardware sales declined for the second consecutive quarter. Consumer demand for personal computers and smartphones has weakened, particularly in international markets where currency fluctuations have made devices more expensive for local buyers.

Investment in Artificial Intelligence

Several companies highlighted their growing investment in artificial intelligence capabilities. Research and development spending increased by 15% compared to the same period last year, with a significant portion directed toward machine learning and generative AI systems.

"We are at the beginning of a fundamental shift in how software is built and deployed," one CEO said during the earnings call. "AI is no longer a feature; it is becoming the core of every product we build."

The investment in AI comes with significant costs. Companies reported that building the infrastructure required to train and deploy large language models has put pressure on operating margins. Some executives acknowledged that the returns on these investments may not materialize for several years.

International Market Challenges

Companies operating in international markets faced headwinds from currency fluctuations and regulatory changes. The strong dollar has made US-produced products more expensive in foreign markets, while new European regulations on data privacy and AI governance have increased compliance costs.

"The regulatory environment in Europe is becoming increasingly complex," noted one CFO. "We are committed to complying with all regulations, but the cost of compliance is growing and we need to factor that into our planning."

In China, continued competition from local providers and ongoing trade tensions have created additional challenges. Several companies reported declining market share in the region, though some segments showed modest growth.

Supply Chain Improvements

Supply chain conditions improved compared to the previous year, when semiconductor shortages and logistics disruptions had limited product availability. Most companies reported that they are now able to meet demand without significant delays, though some specialized components remain in short supply.

"Supply chain resilience has been a major focus for us," said one operations executive. "We have diversified our supplier base and increased inventory levels for critical components. While this adds cost, it also provides stability."

Cybersecurity Investment

Cybersecurity remained a top priority for technology companies, with several firms reporting double-digit increases in security spending. The growing sophistication of cyber attacks, particularly those targeting cloud infrastructure and AI systems, has prompted companies to invest heavily in both defensive technologies and skilled personnel.

"We are seeing a fundamental change in the threat landscape," said one chief information security officer. "The attacks are becoming more targeted, more persistent, and increasingly automated. Our defensive measures have to evolve at the same pace." Companies also reported growing demand from enterprise customers for enhanced security features, particularly in industries such as finance, healthcare, and critical infrastructure where the consequences of a breach can be severe.

Sustainability Initiatives

Environmental sustainability commitments received renewed attention during the earnings calls. Several companies announced accelerated timelines for achieving carbon neutrality, while others highlighted progress in reducing the energy intensity of their data centers and computing infrastructure.

"We have committed to being carbon negative by the end of the decade," one sustainability lead noted. "This is not just about reputation; it is increasingly a competitive requirement, particularly when bidding for large enterprise contracts that include sustainability criteria." Companies acknowledged that meeting these commitments will require continued investment in renewable energy, more efficient cooling systems, and advances in chip design that reduce power consumption without sacrificing performance.

Looking Ahead

Company executives expressed cautious optimism about the coming quarters. While macroeconomic conditions remain uncertain, the long-term trends driving technology adoption appear intact. Cloud computing, artificial intelligence, and cybersecurity are expected to remain key growth areas.

"We are managing the business for the long term," one CEO concluded. "There will be ups and downs along the way, but the digital transformation that is underway across industries is real and it is accelerating. Our job is to execute well and deliver value to our customers and shareholders."

The earnings reports come at a time of significant change in the technology industry. Companies are navigating shifting consumer preferences, evolving regulatory landscapes, and rapid technological change. How they respond to these challenges will shape the industry for years to come.
"""

# BBC 文章来源 URL（占位，真实 cd6684a0 的 source_url 可能不同）
BBC_SOURCE_URL = "https://www.bbc.com/news/technology-2024-quarterly-results"


# 预期 reading units 切分特征（用于 Base Builder fixture，非强制）
# 真实 cd6684a0 是 37 个 unit + 46 个 anchor_segments
# 本 fixture 的文本应能产生类似规模的 unit 切分
EXPECTED_UNIT_COUNT_APPROX = 30  # 容差 ±10
EXPECTED_ANCHOR_COUNT_APPROX = 40


def assert_fixture_text_length() -> None:
    """验证 fixture 文本长度符合预期（约 6064 chars）"""
    actual = len(BBC_ARTICLE_TEXT)
    # 容差 ±200 chars（允许微调）
    assert 5800 <= actual <= 6300, (
        f"BBC_ARTICLE_TEXT length {actual} outside expected range [5800, 6300]. "
        "Adjust fixture text to match cd6684a0's ~6064 chars."
    )
