const SELF_FOCUS_PROPS = new Set(["focusable", "onActivate", "onOKButton"]);
const NATIVE_FOCUS_ELEMENTS = new Set(["button", "input", "select", "textarea"]);

function attributeName(attribute) {
  return attribute.type === "JSXAttribute" && attribute.name.type === "JSXIdentifier" ? attribute.name.name : null;
}

function hasUnknownSpread(openingElement) {
  return openingElement.attributes.some((attribute) => attribute.type === "JSXSpreadAttribute");
}

function hasSelfFocusProp(openingElement) {
  return openingElement.attributes.some((attribute) => {
    const name = attributeName(attribute);
    return name !== null && SELF_FOCUS_PROPS.has(name);
  });
}

function isNativeFocusElement(openingElement) {
  if (openingElement.name.type !== "JSXIdentifier") return false;
  const name = openingElement.name.name;
  if (NATIVE_FOCUS_ELEMENTS.has(name)) return true;
  if (name === "a") return openingElement.attributes.some((attribute) => attributeName(attribute) === "href");
  return openingElement.attributes.some((attribute) => attributeName(attribute) === "tabIndex");
}

function childMayContainFocus(child, focusableBindings) {
  if (child.type === "JSXText") return false;
  if (child.type === "JSXSpreadChild") return true;
  if (child.type === "JSXExpressionContainer") return child.expression.type !== "JSXEmptyExpression";
  if (child.type === "JSXFragment") {
    return child.children.some((nested) => childMayContainFocus(nested, focusableBindings));
  }

  const opening = child.openingElement;
  if (hasUnknownSpread(opening) || hasSelfFocusProp(opening) || isNativeFocusElement(opening)) return true;

  if (opening.name.type !== "JSXIdentifier") return true;
  const name = opening.name.name;
  if (name[0] === name[0]?.toUpperCase() && !focusableBindings.has(name)) return true;

  return child.children.some((nested) => childMayContainFocus(nested, focusableBindings));
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
    const focusableBindings = new Set();

    return {
      ImportDeclaration(node) {
        if (node.source.value !== "@decky/ui") return;
        for (const specifier of node.specifiers) {
          if (
            specifier.type === "ImportSpecifier" &&
            specifier.imported.type === "Identifier" &&
            specifier.imported.name === "Focusable"
          ) {
            focusableBindings.add(specifier.local.name);
          }
        }
      },
      JSXElement(node) {
        const opening = node.openingElement;
        if (opening.name.type !== "JSXIdentifier" || !focusableBindings.has(opening.name.name)) return;
        if (hasUnknownSpread(opening) || hasSelfFocusProp(opening)) return;
        if (node.children.some((child) => childMayContainFocus(child, focusableBindings))) return;

        context.report({ node: opening, messageId: "unreachableRow" });
      },
    };
  },
};
