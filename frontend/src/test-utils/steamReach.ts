/**
 * Which of Steam's internals the shipped code reaches, read off the type checker.
 *
 * A reach is a property read, a call, an element access whose key is a string
 * literal or a union of them, a destructuring, or an object-literal property
 * written where a Steam type is expected. It counts when the member it resolves
 * to is declared in one of three places:
 *
 * - `types/steam.d.ts`'s global declarations (its `declare module` blocks are
 *   React's, not Steam's);
 * - `@decky/ui`'s `dist/globals/` and the `Window` augmentation in its
 *   `dist/webpack.d.ts`, which declare a further twenty of Steam's globals with
 *   types of their own — so a reach typed by them compiles without
 *   `steam.d.ts` naming it;
 * - a type declared in shipped source and reached from a cast of a window
 *   value, following type references within the cast's own file only. That
 *   rule is what keeps a type of Tender's own, used inside such a shape
 *   (`api/host.ts`'s `ToastData`), out of the answer.
 *
 * A member whose type is an inline type literal is a container, not a reach:
 * its members are.
 *
 * A member is named by its path: the nearest named declaration and the chain of
 * properties below it (`SteamClient.Apps.AddShortcut`,
 * `SteamNotificationStore.ProcessNotification`), with `window` for a type
 * literal written in the cast itself (`window.g_PopupManager`).
 */

import { relative } from "node:path";

import ts from "typescript";

export interface SteamReach {
  /** Every reached path, with the source files (relative to `src/`) that reach it. */
  readonly reached: ReadonlyMap<string, ReadonlySet<string>>;
  /** Every member `types/steam.d.ts` declares, as a path. */
  readonly declared: readonly string[];
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

function memberPath(member: Member): string {
  const source = member.getSourceFile();
  const parts = [member.name.getText(source)];
  for (const node of ancestors(member)) {
    if (isMember(node)) {
      parts.unshift(node.name.getText(source));
    } else if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isVariableDeclaration(node)) {
      parts.unshift(node.name.getText(source));
      break;
    } else if (ts.isAsExpression(node)) {
      parts.unshift("window");
      break;
    }
  }
  return parts.join(".");
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
    if (ts.isTypeReferenceNode(node)) {
      let symbol = checker.getSymbolAtLocation(node.typeName);
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

export function scanSteamReach(frontendDir: string): SteamReach {
  const srcDir = `${frontendDir}/src`;
  const steamDts = `${srcDir}/types/steam.d.ts`;
  const program = shippedProgram(frontendDir);
  const checker = program.getTypeChecker();
  const sources = program
    .getSourceFiles()
    .filter((source) => !source.isDeclarationFile && source.fileName.startsWith(`${srcDir}/`));

  const shapes = new Set<ts.Node>();
  const unreadableCastFiles = new Set<string>();
  for (const source of sources) {
    const visit = (node: ts.Node): void => {
      const cast = windowCast(node, checker);
      if (cast) {
        if (checker.getTypeFromTypeNode(cast.type).getProperties().length === 0) {
          unreadableCastFiles.add(relative(srcDir, source.fileName));
        }
        castShapes(cast.type, checker, shapes);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }

  const counts = (member: Member): boolean => {
    if (isContainer(member)) return false;
    const file = member.getSourceFile().fileName;
    if (file === steamDts) return !insideModuleBlock(member);
    if (DECKY_GLOBALS.test(file)) return true;
    return file.startsWith(`${srcDir}/`) && insideShape(member, shapes);
  };

  const reached = new Map<string, Set<string>>();
  for (const source of sources) {
    const file = relative(srcDir, source.fileName);
    const visit = (node: ts.Node): void => {
      for (const symbol of reachedSymbols(node, checker)) {
        for (const declaration of symbol?.declarations ?? []) {
          if (!isMember(declaration) || !counts(declaration)) continue;
          const path = memberPath(declaration);
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
  const dts = program.getSourceFile(steamDts);
  if (!dts) throw new Error(`${steamDts} is not in the program`);
  const collect = (node: ts.Node): void => {
    if (isMember(node) && !isContainer(node) && !insideModuleBlock(node)) declared.push(memberPath(node));
    ts.forEachChild(node, collect);
  };
  collect(dts);

  return { reached, declared, unreadableCastFiles, sourceFiles: sources.length };
}

/**
 * The words a source file spells in code: its identifiers, and the text of its
 * string and template literals. Comments are not code, so a name only a comment
 * mentions is not reached.
 */
export function codeWords(path: string): {
  readonly identifiers: ReadonlySet<string>;
  readonly strings: readonly string[];
} {
  const text = ts.sys.readFile(path);
  if (text === undefined) throw new Error(`${path} cannot be read`);
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, false, ts.ScriptKind.TSX);
  const identifiers = new Set<string>();
  const strings: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) identifiers.add(node.text);
    else if (ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node)) strings.push(node.text);
    ts.forEachChild(node, visit);
  };
  visit(source);
  return { identifiers, strings };
}
