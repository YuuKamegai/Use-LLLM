const assert = require("node:assert/strict");
globalThis.katex = require("../src/use_lllm/static/vendor/katex.min.js");
const markdown = require("../src/use_lllm/general/static/markdown.js");

const rendered = markdown.renderMarkdown("# 見出し\n\n**強調** と `code`\n\n```js\nalert('<x>')\n```");
assert.match(rendered, /<h1>見出し<\/h1>/);
assert.match(rendered, /<strong>強調<\/strong>/);
assert.match(rendered, /copy-code/);
assert.doesNotMatch(rendered, /<x>/);
assert.match(rendered, /&lt;x&gt;/);
assert.match(markdown.renderMarkdown("A | B\n--- | ---\n1 | 2"), /<table>/);

const displayMath = markdown.renderMarkdown(String.raw`$$k = A \cdot e^{-E_a/(RT)}$$`);
assert.match(displayMath, /class="katex-display"/);
assert.doesNotMatch(displayMath, /\$\$/);

const inlineMath = markdown.renderMarkdown(String.raw`速度は \(k = A e^{-E_a/(RT)}\) です。`);
assert.match(inlineMath, /class="katex"/);
assert.match(inlineMath, /速度は/);

const bracketMath = markdown.renderMarkdown(String.raw`\[E = mc^2\]`);
assert.match(bracketMath, /class="katex-display"/);

const codeMath = markdown.renderMarkdown("```tex\n$$E = mc^2$$\n```");
assert.doesNotMatch(codeMath, /class="katex"/);
assert.match(codeMath, /\$\$E = mc\^2\$\$/);

assert.doesNotThrow(() => markdown.renderMarkdown(String.raw`$$\notARealCommand{x}$$`));
assert.doesNotMatch(markdown.renderMarkdown("<img src=x onerror=alert(1)>"), /<img/);

const katex = globalThis.katex;
globalThis.katex = undefined;
assert.match(markdown.renderMarkdown(String.raw`\(x < y\)`), /math-fallback/);
assert.match(markdown.renderMarkdown(String.raw`\(x < y\)`), /&lt;/);
globalThis.katex = katex;
