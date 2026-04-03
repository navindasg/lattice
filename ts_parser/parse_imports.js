/**
 * parse_imports.js — ts-morph based TypeScript/JavaScript import parser.
 *
 * Usage:
 *   node parse_imports.js <file_path> [tsconfig_path]
 *
 * Outputs JSON to stdout:
 *   {
 *     imports: [{ module, import_type, names, is_external, line_number, resolved_path, raw_expression }],
 *     exports: [string]
 *   }
 *
 * Exit codes:
 *   0 — success
 *   1 — error (message on stderr)
 *
 * NOTE: Do NOT use console.log — it adds a trailing newline that can break JSON
 * parsing downstream. Use process.stdout.write() for output.
 */

'use strict';

const { Project, SyntaxKind } = require('ts-morph');
const path = require('path');
const fs = require('fs');

const filePath = process.argv[2];
const tsConfigPath = process.argv[3] || null;

if (!filePath) {
  process.stderr.write('Usage: node parse_imports.js <file_path> [tsconfig_path]\n');
  process.exit(1);
}

const absoluteFilePath = path.resolve(filePath);

if (!fs.existsSync(absoluteFilePath)) {
  process.stderr.write(`File not found: ${absoluteFilePath}\n`);
  process.exit(1);
}

/**
 * Determine whether a module specifier is external (third-party or node built-in).
 * A specifier is internal/relative if it starts with '.' or '/'.
 */
function isExternal(moduleSpecifier) {
  return !moduleSpecifier.startsWith('.') && !moduleSpecifier.startsWith('/');
}

/**
 * Extract the source text of a node for raw_expression.
 */
function getRawText(node) {
  return node.getText();
}

try {
  /** @type {import('ts-morph').ProjectOptions} */
  const projectOptions = {
    skipAddingFilesFromTsConfig: true,
    compilerOptions: {
      allowJs: true,
      allowSyntheticDefaultImports: true,
    },
  };

  // If a tsconfig path is provided, use it for compiler settings (path alias resolution)
  const resolvedTsConfig = tsConfigPath ? path.resolve(tsConfigPath) : null;
  const project = resolvedTsConfig
    ? new Project({ tsConfigFilePath: resolvedTsConfig, skipAddingFilesFromTsConfig: true })
    : new Project(projectOptions);

  const sourceFile = project.addSourceFileAtPath(absoluteFilePath);

  const imports = [];
  const exports = [];

  // -----------------------------------------------------------------------
  // 1. Static ES module imports: import { x } from './module'
  // -----------------------------------------------------------------------
  for (const decl of sourceFile.getImportDeclarations()) {
    const moduleSpecifier = decl.getModuleSpecifierValue();
    const lineNumber = decl.getStartLineNumber();
    const named = decl.getNamedImports().map(n => n.getName());
    const defaultImport = decl.getDefaultImport();
    if (defaultImport) {
      named.unshift(defaultImport.getText());
    }
    const namespaceImport = decl.getNamespaceImport();
    if (namespaceImport) {
      named.push(`* as ${namespaceImport.getText()}`);
    }

    const importType = moduleSpecifier.startsWith('.') ? 'relative' : 'standard';

    imports.push({
      module: moduleSpecifier,
      import_type: importType,
      names: named,
      is_external: isExternal(moduleSpecifier),
      line_number: lineNumber,
      resolved_path: null,
      raw_expression: null,
    });
  }

  // -----------------------------------------------------------------------
  // 2. CommonJS require() calls and dynamic import() expressions
  //    forEachDescendant traversal — getImportDeclarations() misses both.
  // -----------------------------------------------------------------------
  sourceFile.forEachDescendant((node) => {
    // Check for CallExpression nodes
    if (node.getKind() !== SyntaxKind.CallExpression) {
      return;
    }

    const callExpr = node.asKindOrThrow(SyntaxKind.CallExpression);
    const expression = callExpr.getExpression();
    const args = callExpr.getArguments();

    // --- CommonJS: require('module') ---
    if (
      expression.getKind() === SyntaxKind.Identifier &&
      expression.getText() === 'require' &&
      args.length > 0
    ) {
      const firstArg = args[0];
      if (firstArg.getKind() === SyntaxKind.StringLiteral) {
        const moduleSpecifier = firstArg.getLiteralText();
        const lineNumber = callExpr.getStartLineNumber();
        const importType = moduleSpecifier.startsWith('.') ? 'relative' : 'standard';

        imports.push({
          module: moduleSpecifier,
          import_type: importType,
          names: [],
          is_external: isExternal(moduleSpecifier),
          line_number: lineNumber,
          resolved_path: null,
          raw_expression: null,
        });
      }
      return;
    }

    // --- Dynamic import(): import('./module') or import('module') ---
    // Dynamic imports are represented as ImportType CallExpressions in ts-morph.
    // The expression kind is SyntaxKind.ImportKeyword for import() calls.
    if (expression.getKind() === SyntaxKind.ImportKeyword && args.length > 0) {
      const firstArg = args[0];
      const lineNumber = callExpr.getStartLineNumber();
      let moduleSpecifier = '<dynamic>';
      if (firstArg.getKind() === SyntaxKind.StringLiteral) {
        moduleSpecifier = firstArg.getLiteralText();
      }
      const rawExpression = getRawText(callExpr);
      const importType = 'dynamic';

      imports.push({
        module: moduleSpecifier,
        import_type: importType,
        names: [],
        is_external: isExternal(moduleSpecifier),
        line_number: lineNumber,
        resolved_path: null,
        raw_expression: rawExpression,
      });
    }
  });

  // -----------------------------------------------------------------------
  // 3. Exports: barrel re-exports (export * from) and named re-exports
  // -----------------------------------------------------------------------
  for (const decl of sourceFile.getExportDeclarations()) {
    const moduleSpecifier = decl.getModuleSpecifierValue();
    const namedExports = decl.getNamedExports().map(n => n.getName());

    if (decl.isNamespaceExport()) {
      // export * from './routes'
      exports.push(`* from '${moduleSpecifier}'`);
    } else if (namedExports.length > 0) {
      // export { helper } from './utils'
      for (const name of namedExports) {
        exports.push(moduleSpecifier ? `${name} from '${moduleSpecifier}'` : name);
      }
    }
  }

  // Named exported declarations (functions, classes, variables, etc.)
  for (const [name] of sourceFile.getExportedDeclarations()) {
    // Only include direct declarations, not re-exports from other modules
    if (!exports.some(e => e === name || e.startsWith(`${name} from`))) {
      exports.push(name);
    }
  }

  const result = { imports, exports };
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
} catch (err) {
  process.stderr.write(`Error parsing ${filePath}: ${err.message}\n`);
  process.exit(1);
}
