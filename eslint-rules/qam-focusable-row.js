const SELF_FOCUS_PROPS = new Set(["focusable", "onActivate", "onOKButton"]);
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

function spreadMayProvideFocus(argument) {
  const expression = unwrapExpression(argument);
  if (expression.type !== "ObjectExpression") return true;

  return expression.properties.some((property) => {
    if (property.type === "SpreadElement") return spreadMayProvideFocus(property.argument);
    const name = propertyName(property);
    return name === null || SELF_FOCUS_PROPS.has(name);
  });
}

function hasPossibleSelfFocus(openingElement) {
  return openingElement.attributes.some((attribute) => {
    const name = attributeName(attribute);
    if (name !== null) return SELF_FOCUS_PROPS.has(name);
    return attribute.type === "JSXSpreadAttribute" && spreadMayProvideFocus(attribute.argument);
  });
}

function isNativeFocusElement(openingElement) {
  if (openingElement.name.type !== "JSXIdentifier") return false;
  const name = openingElement.name.name;
  if (NATIVE_FOCUS_ELEMENTS.has(name)) return true;
  if (name === "a") return openingElement.attributes.some((attribute) => attributeName(attribute) === "href");
  return openingElement.attributes.some((attribute) => attributeName(attribute) === "tabIndex");
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
        element !== null &&
        (element.type === "SpreadElement" || expressionMayContainFocus(element, isDeckyFocusable)),
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
  if (hasPossibleSelfFocus(opening) || isNativeFocusElement(opening)) return true;

  if (opening.name.type !== "JSXIdentifier") return true;
  const name = opening.name.name;
  if (name[0] === name[0]?.toUpperCase() && !isDeckyFocusable(opening)) return true;

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
        if (hasPossibleSelfFocus(opening)) return;
        if (node.children.some((child) => childMayContainFocus(child, isDeckyFocusable))) return;

        context.report({ node: opening, messageId: "unreachableRow" });
      },
    };
  },
};
