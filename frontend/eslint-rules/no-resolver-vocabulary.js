import path from "node:path";

const BY_FILE = { type: "object", additionalProperties: { type: "array", items: { type: "string" } } };

/** The name a member read, an object key or a type's property states, or `null` when it is computed. */
function keyName(key, computed) {
  if (key.type === "Identifier" && !computed) return key.name;
  if (key.type === "Literal" && typeof key.value === "string") return key.value;
  return null;
}

export default {
  meta: {
    type: "problem",
    docs: {
      description: "forbid the resolver's codes and answer field names outside the files listed as reading them today",
    },
    schema: [
      {
        type: "object",
        properties: {
          codes: { type: "array", items: { type: "string" } },
          names: { type: "array", items: { type: "string" } },
          readers: BY_FILE,
          notTheResolvers: BY_FILE,
          generic: { type: "object", additionalProperties: { type: "string" } },
        },
        required: ["codes", "names", "readers", "notTheResolvers", "generic"],
        additionalProperties: false,
      },
    ],
    messages: {
      code: "'{{token}}' is one of the resolver's codes or the value of one of its closed vocabularies. The panel words Tender's own marks, which the backend's reading module decides; it never reads the resolver's codes or values.",
      name: "'{{token}}' is one of the resolver's answer field names. The panel reads Tender's own view of the answer, which the backend's reading module builds; it never reads the resolver's fields.",
    },
  },
  create(context) {
    const [{ codes, names, readers, notTheResolvers, generic }] = context.options;
    const file = path.relative(context.cwd, context.filename).split(path.sep).join("/");
    const here = new Set([...(readers[file] ?? []), ...(notTheResolvers[file] ?? []), ...Object.keys(generic)]);
    const watchedCodes = new Set(codes.filter((code) => !here.has(code)));
    const watchedNames = new Set(names.filter((name) => !here.has(name)));

    const checkCode = (node, value) => {
      if (typeof value === "string" && watchedCodes.has(value)) {
        context.report({ node, messageId: "code", data: { token: value } });
      }
    };
    const checkName = (node, name) => {
      if (name !== null && watchedNames.has(name)) context.report({ node, messageId: "name", data: { token: name } });
    };
    // A quoted key is a `Literal` and checked as a code there; Prettier strips
    // the quotes off any key that needs none, so the unquoted spelling is
    // checked as a code here, unless it is reported as a name already.
    const checkKey = (key, computed) => {
      const name = keyName(key, computed);
      if (watchedNames.has(name)) checkName(key, name);
      else if (key.type === "Identifier" && !computed) checkCode(key, name);
    };

    return {
      Literal(node) {
        checkCode(node, node.value);
      },
      TemplateElement(node) {
        checkCode(node, node.value.cooked);
      },
      MemberExpression(node) {
        checkKey(node.property, node.computed);
      },
      Property(node) {
        checkKey(node.key, node.computed);
      },
      TSPropertySignature(node) {
        checkKey(node.key, node.computed);
      },
    };
  },
};
