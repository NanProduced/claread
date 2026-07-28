# Safe HTML Adaptation Notes

The committee reviewed the adaptation pipeline and confirmed that every cleaning step must keep the reading content visible, preserve the author's wording, and record what changed so learners never lose meaning silently.

<script>alert("xss")</script>

<iframe src="https://evil.example/embed"></iframe>

An inline image <img src="tracker.png" onerror="steal()"> sits inside this sentence and must be neutralized.

A [javascript link](javascript:alert(1)) and a [data link](data:text/html;base64,PHNjcmlwdA==) and a [vbscript link](vbscript:msgbox(1)) must keep their text.

Safe links stay intact: [https site](https://example.com/docs), [http site](http://example.com), and [mail](mailto:test@example.com).

C++ developers write vector<T> and template <name> placeholders in prose all the time.

<aside class="note">This aside carries a genuine reading note about the chapter summary.</aside>

The final paragraph adds enough natural English words so the suitability gate sees a genuine reading document rather than a short suspicious snippet, and it keeps the discussion focused on learning value for the reader.
