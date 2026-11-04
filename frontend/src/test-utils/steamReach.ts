/**
 * Which of Steam's internals the shipped code reaches, read off the type
 * checker, each named by its path. What counts as a reach, the three places a
 * reached member may be declared, and how a path is derived are
 * `docs/architecture/frontend-bundles.md`'s, "Every Steam internal Tender
 * reaches".
 */

import { relative } from "node:path";

import ts from "typescript";

export interface SteamReach {
  /** Every reached path, with the source files (relative to `src/`) that reach it. */
  readonly reached: ReadonlyMap<string, ReadonlySet<string>>;
  /** Every member `types/steam.d.ts` declares, as a path. */
  readonly declared: readonly string[];
  /** The types holding a counted member that no route from Steam's roots reaches, by name. */
  readonly unrouted: ReadonlySet<string>;
  /** The files holding a cast of a window value to a type with no members to read — a `Record`, `unknown`. */
  readonly unreadableCastFiles: ReadonlySet<string>;
  /** How many source files the program checked: a scan over nothing passes always. */
  readonly sourceFiles: number;
}

const DECKY_GLOBALS = /[\\/]@decky[\\/]ui[\\/]dist[\\/](?:globals[\\/].*|webpack\.d\.ts)$/;

const isShipped = (file: string): boolean =>
  !/\.(test|spec)\.tsx?$/.test(file) && !/[\\/]test-utils[\\/]/.test(file) && !/[\\/]test-setup\.ts$/.test(file);

type Member = ts.PropertySignature | ts.MethodSignature;

const isMember = (node: ts.Node): node is Member => ts.isPropertySignature(node) || ts.isMethodSignature(node);

const isContainer = (member: Member): boolean =>
  ts.isPropertySignature(member) && member.type !== undefined && ts.isTypeLiteralNode(member.type);

/** A node's ancestors, innermost first. `parent` is typed as always set, and a source file has none. */
function* ancestors(node: ts.Node): Generator<ts.Node> {
  for (let parent = node.parent as ts.Node | undefined; parent; parent = parent.parent as ts.Node | undefined) {
    yield parent;
  }
}

function insideModuleBlock(node: ts.Node): boolean {
  for (const parent of ancestors(node)) if (ts.isModuleDeclaration(parent)) return true;
  return false;
}

/** The program over every shipped source file `tsconfig.json` names. */
function shippedProgram(frontendDir: string): ts.Program {
  const configPath = `${frontendDir}/tsconfig.json`;
  const config = ts.readConfigFile(configPath, (path) => ts.sys.readFile(path));
  if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, "\n"));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, frontendDir);
  return ts.createProgram(parsed.fileNames.filter(isShipped), parsed.options);
}

/** The outermost cast whose innermost operand is a window value, with that cast's target. */
function windowCast(node: ts.Node, checker: ts.TypeChecker): ts.AsExpression | undefined {
  if (!ts.isAsExpression(node)) return undefined;
  let parent = node.parent;
  while (ts.isParenthesizedExpression(parent)) parent = parent.parent;
  if (ts.isAsExpression(parent)) return undefined;
  let operand: ts.Expression = node.expression;
  while (ts.isAsExpression(operand) || ts.isParenthesizedExpression(operand)) operand = operand.expression;
  const type = checker.typeToString(checker.getTypeAtLocation(operand));
  return type === "Window" || type === "Window & typeof globalThis" || type === "typeof globalThis" ? node : undefined;
}

/** The type declarations a cast's target reaches within its own file, the target's own type literal included. */
function castShapes(target: ts.TypeNode, checker: ts.TypeChecker, into: Set<ts.Node>): void {
  const file = target.getSourceFile().fileName;
  const visit = (node: ts.Node): void => {
    if (ts.isTypeLiteralNode(node)) into.add(node);
    if (ts.isTypeReferenceNode(node) || ts.isExpressionWithTypeArguments(node)) {
      let symbol = checker.getSymbolAtLocation(ts.isTypeReferenceNode(node) ? node.typeName : node.expression);
      if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
      for (const declaration of symbol?.declarations ?? []) {
        const isShape = ts.isInterfaceDeclaration(declaration) || ts.isTypeAliasDeclaration(declaration);
        if (isShape && declaration.getSourceFile().fileName === file && !into.has(declaration)) {
          into.add(declaration);
          ts.forEachChild(declaration, visit);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(target);
}

function insideShape(member: Member, shapes: ReadonlySet<ts.Node>): boolean {
  for (const node of ancestors(member)) if (shapes.has(node)) return true;
  return false;
}

/** The symbols a node reaches, by each of the shapes a reach is written in. */
function reachedSymbols(node: ts.Node, checker: ts.TypeChecker): (ts.Symbol | undefined)[] {
  if (ts.isPropertyAccessExpression(node)) return [checker.getSymbolAtLocation(node.name)];
  if (ts.isElementAccessExpression(node)) {
    const key = checker.getTypeAtLocation(node.argumentExpression);
    const target = checker.getNonNullableType(checker.getTypeAtLocation(node.expression));
    return (key.isUnion() ? key.types : [key])
      .filter((part): part is ts.StringLiteralType => part.isStringLiteral())
      .map((part) => checker.getPropertyOfType(target, part.value));
  }
  if (
    (ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node)) &&
    ts.isObjectLiteralExpression(node.parent)
  ) {
    const expected = checker.getContextualType(node.parent);
    if (!expected || ts.isComputedPropertyName(node.name)) return [];
    return [checker.getPropertyOfType(checker.getNonNullableType(expected), node.name.text)];
  }
  if (ts.isBindingElement(node) && ts.isObjectBindingPattern(node.parent)) {
    const name = node.propertyName ?? node.name;
    if (!ts.isIdentifier(name)) return [];
    return [checker.getPropertyOfType(checker.getTypeAtLocation(node.parent), name.text)];
  }
  return [];
}

const isReference = (type: ts.Type): type is ts.TypeReference =>
  (type.flags & ts.TypeFlags.Object) !== 0 && ((type as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference) !== 0;

const ELEMENT_HOLDERS = new Set(["Array", "ReadonlyArray", "Iterable", "IterableIterator", "Set", "ReadonlySet"]);

/** The interface and type-literal declarations a type is made of, its base interfaces' included. */
function declarationsOf(type: ts.Type, checker: ts.TypeChecker): ts.Node[] {
  const own = (type.getSymbol()?.declarations ?? []).filter(
    (declaration) => ts.isInterfaceDeclaration(declaration) || ts.isTypeLiteralNode(declaration),
  );
  const bases = type.isClassOrInterface() ? checker.getBaseTypes(type) : [];
  return [...own, ...bases.flatMap((base) => declarationsOf(base, checker))];
}

/**
 * Each type's route from Steam's roots, keyed by the declarations it is made
 * of: a breadth-first walk, so the first route recorded is the shortest, and
 * among routes of one length the one from the earlier root. The declarations
 * of the global scope itself (`interface Window`) are named by no route: a
 * window-typed field reached on the way does not rename them.
 */
function routes(
  roots: readonly (readonly [string, ts.Type])[],
  globalScope: readonly ts.Node[],
  checker: ts.TypeChecker,
  descends: (type: ts.Type) => boolean,
): Map<ts.Node, string> {
  const found = new Map<ts.Node, string>(globalScope.map((declaration) => [declaration, ""]));
  const seen = new Set<ts.Type>();
  const queue: (readonly [ts.Type, string])[] = [...roots.map(([name, type]) => [type, name] as const)];
  const member = (route: string, name: string): string => (route === "" ? name : `${route}.${name}`);
  for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
    const [whole, route] = next;
    const nonNull = checker.getNonNullableType(whole);
    for (const type of nonNull.isUnion() ? nonNull.types : [nonNull]) {
      if (seen.has(type)) continue;
      seen.add(type);
      for (const declaration of declarationsOf(type, checker))
        if (!found.has(declaration)) found.set(declaration, route);
      const holder = type.getSymbol()?.name ?? "";
      const [argument] = isReference(type) ? checker.getTypeArguments(type) : [];
      if (holder === "Promise") {
        // The awaited value takes no step: at the front, so the walk stays shortest-first.
        if (argument) queue.unshift([argument, route]);
        continue;
      }
      if (ELEMENT_HOLDERS.has(holder)) {
        if (argument) queue.push([argument, `${route}[]`]);
        continue;
      }
      if (!descends(type)) continue;
      for (const signature of type.getCallSignatures()) {
        queue.push([signature.getReturnType(), `${route}()`]);
        for (const parameter of signature.getParameters()) {
          queue.push([checker.getTypeOfSymbol(parameter), `${route}(${parameter.name})`]);
        }
      }
      for (const property of type.getProperties()) {
        queue.push([checker.getTypeOfSymbol(property), member(route, property.name)]);
      }
    }
  }
  return found;
}

export function scanSteamReach(frontendDir: string): SteamReach {
  const srcDir = `${frontendDir}/src`;
  const steamDts = `${srcDir}/types/steam.d.ts`;
  const program = shippedProgram(frontendDir);
  const checker = program.getTypeChecker();
  const sources = program
    .getSourceFiles()
    .filter((source) => !source.isDeclarationFile && source.fileName.startsWith(`${srcDir}/`));
  const dts = program.getSourceFile(steamDts);
  if (!dts) throw new Error(`${steamDts} is not in the program`);

  const roots: (readonly [string, ts.Type])[] = [];
  for (const statement of dts.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      roots.push([declaration.name.getText(dts), checker.getTypeAtLocation(declaration)]);
    }
  }
  const ownGlobals = new Set(roots.map(([name]) => name));

  const shapes = new Set<ts.Node>();
  const unreadableCastFiles = new Set<string>();
  for (const source of sources) {
    const visit = (node: ts.Node): void => {
      const cast = windowCast(node, checker);
      if (cast) {
        const target = checker.getTypeFromTypeNode(cast.type);
        if (target.getProperties().length === 0) unreadableCastFiles.add(relative(srcDir, source.fileName));
        roots.push(["", target]);
        castShapes(cast.type, checker, shapes);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  const windowSymbol = checker.resolveName("window", undefined, ts.SymbolFlags.Value, false);
  const windowType = windowSymbol ? checker.getTypeOfSymbol(windowSymbol) : undefined;
  for (const property of windowType?.getProperties() ?? []) {
    const deckys = (property.declarations ?? []).some((d) => DECKY_GLOBALS.test(d.getSourceFile().fileName));
    if (deckys && !ownGlobals.has(property.name)) roots.push([property.name, checker.getTypeOfSymbol(property)]);
  }

  const counts = (member: Member): boolean => {
    if (isContainer(member)) return false;
    const file = member.getSourceFile().fileName;
    if (file === steamDts) return !insideModuleBlock(member);
    if (DECKY_GLOBALS.test(file)) return true;
    return file.startsWith(`${srcDir}/`) && insideShape(member, shapes);
  };

  const descends = (type: ts.Type): boolean =>
    (type.getSymbol()?.declarations ?? []).some((declaration) => {
      const file = declaration.getSourceFile().fileName;
      return file.startsWith(`${srcDir}/`) || DECKY_GLOBALS.test(file);
    });
  const windowInterface = checker.resolveName("Window", undefined, ts.SymbolFlags.Type, false);
  const routeOf = routes(roots, windowInterface?.declarations ?? [], checker, descends);

  const unrouted = new Set<string>();
  const pathOf = (member: Member): string | undefined => {
    const route = routeOf.get(member.parent);
    const name = member.name.getText(member.getSourceFile());
    if (route === undefined) {
      const holder = member.parent;
      unrouted.add(ts.isInterfaceDeclaration(holder) ? holder.name.text : `a type literal holding ${name}`);
      return undefined;
    }
    return route === "" ? name : `${route}.${name}`;
  };

  const reached = new Map<string, Set<string>>();
  for (const source of sources) {
    const file = relative(srcDir, source.fileName);
    const visit = (node: ts.Node): void => {
      for (const symbol of reachedSymbols(node, checker)) {
        for (const declaration of symbol?.declarations ?? []) {
          if (!isMember(declaration) || !counts(declaration)) continue;
          const path = pathOf(declaration);
          if (path === undefined) continue;
          const files = reached.get(path) ?? new Set<string>();
          files.add(file);
          reached.set(path, files);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  const declared: string[] = [];
  const collect = (node: ts.Node): void => {
    if (isMember(node) && !isContainer(node) && !insideModuleBlock(node)) {
      const path = pathOf(node);
      if (path !== undefined) declared.push(path);
    }
    ts.forEachChild(node, collect);
  };
  collect(dts);

  return { reached, declared, unrouted, unreadableCastFiles, sourceFiles: sources.length };
}

/**
 * The words a source file spells in code: its identifiers, the text of its
 * string, template and regex literals, and the literal paths it hands a
 * `Navigate` call. Comments are not code, so a name only a comment mentions is
 * not reached.
 */
export function codeWords(path: string): {
  readonly identifiers: ReadonlySet<string>;
  readonly strings: readonly string[];
  readonly navigated: readonly string[];
} {
  const text = ts.sys.readFile(path);
  if (text === undefined) throw new Error(`${path} cannot be read`);
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, false, ts.ScriptKind.TSX);
  const identifiers = new Set<string>();
  const strings: string[] = [];
  const navigated: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) identifiers.add(node.text);
    else if (ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node) || ts.isRegularExpressionLiteral(node)) {
      strings.push(node.text);
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const [first] = node.arguments;
      if (node.expression.name.text === "Navigate" && first && ts.isStringLiteralLike(first))
        navigated.push(first.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return { identifiers, strings, navigated };
}
