/**
 * The values Steam's `flow-children` parser knows. Any other is dropped, and the
 * group then steers by its CSS — what that means is stated in
 * `docs/architecture/qam-panel.md`, "Focus groups", which owns the fact; this is
 * the copy the rule checks against.
 */
const KNOWN_VALUES = new Set(["column", "column-reverse", "row", "row-reverse", "grid", "geometric"]);

const HOME = 'docs/architecture/qam-panel.md, "Focus groups"';

/** The string an attribute value spells out on its face, or null when it is anything else. */
function literalValue(value) {
  if (value.type === "Literal") return typeof value.value === "string" ? value.value : null;
  if (value.type !== "JSXExpressionContainer") return null;
  const expression = value.expression;
  if (expression.type === "Literal") return typeof expression.value === "string" ? expression.value : null;
  if (expression.type === "TemplateLiteral" && expression.expressions.length === 0) {
    return expression.quasis[0].value.cooked;
  }
  return null;
}

export default {
  meta: {
    type: "problem",
    docs: {
      description: "require every flow-children value to be one Steam's parser knows",
    },
    schema: [],
    messages: {
      unknownValue: `flow-children="{{value}}" is not a value Steam's parser knows: it takes column, column-reverse, row, row-reverse, grid or geometric, and drops any other (${HOME}).`,
      notALiteral: `flow-children takes a string literal here, so that it can be checked against the six values Steam's parser knows (${HOME}).`,
    },
  },
  create(context) {
    return {
      JSXAttribute(node) {
        if (node.name.type !== "JSXIdentifier" || node.name.name !== "flow-children") return;
        const value = node.value === null ? null : literalValue(node.value);
        if (value === null) {
          context.report({ node, messageId: "notALiteral" });
        } else if (!KNOWN_VALUES.has(value)) {
          context.report({ node, messageId: "unknownValue", data: { value } });
        }
      },
    };
  },
};
