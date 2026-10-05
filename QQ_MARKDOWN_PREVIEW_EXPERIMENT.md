# QQ Native Markdown Preview Experiment

This experiment is deliberately separate from the production Daily News push path.

## Official API result

- C2C endpoint: `POST /v2/users/{user_openid}/messages`
- Group endpoint: `POST /v2/groups/{group_openid}/messages`
- Native Markdown payload: `msg_type=2` plus `markdown.content`
- Legacy Markdown template IDs are deprecated.
- The current send-message documentation does not list ARK as an outbound message type. ARK is therefore not tested with an undocumented payload.

Official documentation:

- https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html
- https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html

## Run

Double-click:

`qq-markdown-preview-test.cmd`

The command sends one fixed Markdown sample. It does not read news Events, call Gemini, generate Embeddings, call the production `push()` method, or write to `news.db`.

The tool verifies the SHA-256 of `news.db` before and after sending. Production integration remains unchanged until the phone QQ rendering is accepted manually.

## Verified result

The QQ API accepted the native Markdown message and returned a message ID. The database hash was unchanged. Client-side rendering still needs to be judged from the received message on the user's phone.
