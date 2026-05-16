# @claread/contracts

Shared client-side constants and TypeScript types for Claread multi-client contracts.

Current scope:

- User annotation anchor types: `sentence`, `paragraph`, `text_range`.
- Favorite target types, including Web-created `text_range` targets.
- User annotation color enum.
- `text_range` offset/hash convention.

`text_range` offsets are JavaScript UTF-16 code unit offsets inside a canonical sentence from the render scene. `text_hash` uses `fnv1a32-utf16` over the selected text.
