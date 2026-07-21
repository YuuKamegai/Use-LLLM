const assert = require("node:assert/strict");
const markdown = require("../src/use_lllm/general/static/markdown.js");

const rendered = markdown.renderMarkdown("# 見出し\n\n**強調** と `code`\n\n```js\nalert('<x>')\n```");
assert.match(rendered, /<h1>見出し<\/h1>/);
assert.match(rendered, /<strong>強調<\/strong>/);
assert.match(rendered, /copy-code/);
assert.doesNotMatch(rendered, /<x>/);
assert.match(rendered, /&lt;x&gt;/);
assert.match(markdown.renderMarkdown("A | B\n--- | ---\n1 | 2"), /<table>/);
