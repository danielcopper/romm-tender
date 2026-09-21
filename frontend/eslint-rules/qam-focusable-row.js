/**
 * The props Steam's base panel makes a focus stop out of, on the nav node it
 * renders. Which of them the node reads, and what it answers, is `GetFocusable()`
 * — stated in full in `docs/architecture/qam-panel.md`, which owns that
 * mechanism. `onCancelButton` is deliberately not among them.
 *
 * A plain host element renders no nav node, so these are inert on a `div` and the
 * question is put to the row itself and to a `Focusable` among its descendants.
 */
const NAV_STOP_PROPS = new Set(["focusable", "focusableIfEmpty", "onActivate", "onOKButton"]);

/**
 * What the BROWSER focuses, which is a different question and one only a
 * descendant is asked: `tabIndex` reaches the rendered element rather than the
 * nav node, so it is no stop for Steam's navigation — but a container holding
 * something the browser can focus is not a container this rule can call empty.
 */
const DOM_FOCUS_ATTRS = new Set(["tabIndex"]);

/** The kinds the browser focuses with no attribute at all; an `<a>` counts only with an `href`, below. */
const NATIVE_FOCUS_ELEMENTS = new Set(["button", "input", "select", "textarea"]);

function attributeName(attribute) {
  return attribute.type === "JSXAttribute" && attribute.name.type === "JSXIdentifier" ? attribute.name.name : null;
}

function unwrapExpression(expression) {
  while (
    expression.type === "ChainExpression" ||
    expression.type === "TSAsExpression" ||
    expression.type === "TSNonNullExpression" ||
    expression.type === "TSSatisfiesExpression" ||
    expression.type === "TSTypeAssertion"
  ) {
    expression = expression.expression;
  }
  return expression;
}

function propertyName(property) {
  if (property.computed && property.key.type !== "Literal") return null;
  if (property.key.type === "Identifier") return property.key.name;
  if (property.key.type === "Literal" && typeof property.key.value === "string") return property.key.value;
  return null;
}

// `focusable={false}` declares nothing. Only a literal `false` is judged — `{0}`,
// `{null}` and `{undefined}` are falsy to Steam too and are taken as declarations
// anyway, deliberately: what a row really carries is an optional prop this rule
// cannot evaluate, so judging the three literals nobody writes would buy nothing
// and would read as more than the rule can see.
function isExplicitlyFalse(value) {
  if (value === null || value === undefined) return false;
  const expression = unwrapExpression(value.type === "JSXExpressionContainer" ? value.expression : value);
  return expression.type === "Literal" && expression.value === false;
}

function spreadMayProvide(argument, props) {
  const expression = unwrapExpression(argument);
  if (expression.type !== "ObjectExpression") return true;

  return expression.properties.some((property) => {
    if (property.type === "SpreadElement") return spreadMayProvide(property.argument, props);
    const name = propertyName(property);
    return name === null || (props.has(name) && !isExplicitlyFalse(property.value));
  });
}

function declares(openingElement, props) {
  return openingElement.attributes.some((attribute) => {
    const name = attributeName(attribute);
    if (name !== null) return props.has(name) && !isExplicitlyFalse(attribute.value);
    return attribute.type === "JSXSpreadAttribute" && spreadMayProvide(attribute.argument, props);
  });
}

/** Whether Steam's navigation can stop on the node this element renders. */
function declaresNavStop(openingElement) {
  return declares(openingElement, NAV_STOP_PROPS);
}

/**
 * Whether the browser can focus this element — by its kind, or by a `tabIndex` it
 * was given.
 *
 * The attribute is asked of every kind, which is a deliberate change from what this
 * rule used to answer: the `a`/`href` test returned early, so a `tabIndex` on an
 * `<a>` carrying no `href` was invisible on that one element kind. Restoring that
 * early return restores the blind spot.
 */
function mayTakeDomFocus(openingElement) {
  if (openingElement.name.type === "JSXIdentifier") {
    const name = openingElement.name.name;
    if (NATIVE_FOCUS_ELEMENTS.has(name)) return true;
    if (name === "a" && openingElement.attributes.some((attribute) => attributeName(attribute) === "href")) return true;
  }
  return declares(openingElement, DOM_FOCUS_ATTRS);
}

function expressionMayContainFocus(expression, isDeckyFocusable) {
  expression = unwrapExpression(expression);
  // A template literal coerces every substitution to text, so an opaque one cannot
  // put a focusable node in the child position — unlike an opaque identifier, which can.
  if (
    expression.type === "Literal" ||
    expression.type === "ObjectExpression" ||
    expression.type === "TemplateLiteral"
  ) {
    return false;
  }
  if (expression.type === "JSXElement" || expression.type === "JSXFragment") {
    return childMayContainFocus(expression, isDeckyFocusable);
  }
  if (expression.type === "ArrayExpression") {
    return expression.elements.some(
      (element) =>
        element !== null && (element.type === "SpreadElement" || expressionMayContainFocus(element, isDeckyFocusable)),
    );
  }
  if (expression.type === "ConditionalExpression") {
    return (
      expressionMayContainFocus(expression.consequent, isDeckyFocusable) ||
      expressionMayContainFocus(expression.alternate, isDeckyFocusable)
    );
  }
  if (expression.type === "SequenceExpression") {
    const last = expression.expressions.at(-1);
    return last === undefined || expressionMayContainFocus(last, isDeckyFocusable);
  }
  if (expression.type === "BinaryExpression" || expression.type === "UnaryExpression") return false;
  return true;
}

function childMayContainFocus(child, isDeckyFocusable) {
  if (child.type === "JSXText") return false;
  if (child.type === "JSXSpreadChild") return true;
  if (child.type === "JSXExpressionContainer") {
    return (
      child.expression.type !== "JSXEmptyExpression" && expressionMayContainFocus(child.expression, isDeckyFocusable)
    );
  }
  if (child.type === "JSXFragment") {
    return child.children.some((nested) => childMayContainFocus(nested, isDeckyFocusable));
  }

  const opening = child.openingElement;
  if (mayTakeDomFocus(opening)) return true;

  if (opening.name.type !== "JSXIdentifier") return true;
  const name = opening.name.name;
  // Only a component renders a nav node, so a stop prop counts on one and is
  // inert on a host element; an unknown component is opaque whatever it carries.
  if (name[0] === name[0]?.toUpperCase() && (declaresNavStop(opening) || !isDeckyFocusable(opening))) return true;

  return child.children.some((nested) => childMayContainFocus(nested, isDeckyFocusable));
}

function resolveVariable(sourceCode, node, name) {
  let scope = sourceCode.getScope(node);
  while (scope !== null) {
    const variable = scope.set.get(name);
    if (variable !== undefined) return variable;
    scope = scope.upper;
  }
  return undefined;
}

export default {
  meta: {
    type: "problem",
    docs: {
      description: "require statically empty QAM Focusable rows to declare themselves as focus stops",
    },
    schema: [],
    messages: {
      unreachableRow:
        "This QAM Focusable is neither a declared focus stop nor a container with a statically identifiable focusable descendant.",
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    const focusableBindings = new Set();
    const isDeckyFocusable = (openingElement) => {
      if (openingElement.name.type !== "JSXIdentifier") return false;
      return focusableBindings.has(resolveVariable(sourceCode, openingElement, openingElement.name.name));
    };

    return {
      ImportDeclaration(node) {
        if (node.source.value !== "@decky/ui") return;
        for (const specifier of node.specifiers) {
          if (
            specifier.type === "ImportSpecifier" &&
            specifier.imported.type === "Identifier" &&
            specifier.imported.name === "Focusable"
          ) {
            for (const variable of sourceCode.getDeclaredVariables(specifier)) focusableBindings.add(variable);
          }
        }
      },
      JSXElement(node) {
        const opening = node.openingElement;
        if (!isDeckyFocusable(opening)) return;
        if (declaresNavStop(opening)) return;
        if (node.children.some((child) => childMayContainFocus(child, isDeckyFocusable))) return;

        context.report({ node: opening, messageId: "unreachableRow" });
      },
    };
  },
};
