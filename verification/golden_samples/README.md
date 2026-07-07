Central manifest for the T0.1 / T0.2 reader baseline golden sample set.

Each entry maps a stable sample id to:

- a fixed plain-text article stored under `articles/<id>.txt`
- metadata: shape, expected character band, source attribution
- the chain strategy the sample is meant to exercise

The samples intentionally cover the five shapes called out in
`docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
(M0 acceptance for T0.2):

- `short_news`           : short single-genre news (well under the short-path band)
- `reuters_bbc_970`      : ~970-word Reuters/BBC-style article (the historical
                            baseline reference; see prior D5 short-form
                            diagnosis 22.5K -> 175.6K tokens, 31.5s -> 323.8s
                            on a sample in this band)
- `fragmented_news`      : short news with paragraph / heading fragmentation
                            typical of syndicated feeds
- `long_article`         : single long article that crosses the medium/long
                            boundary without strong section structure
- `long_article_headings`: long article with clear H2 / H3 headings, used to
                            exercise the section-oriented longform path later
                            (M5); included now so the baseline has a fixed
                            fixture before that work lands

These samples are read-only fixtures. They are not committed to any
production DB; they exist so the baseline harness has a stable corpus
across runs.
