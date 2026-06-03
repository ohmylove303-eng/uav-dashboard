import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontendRoot = path.join(repoRoot, 'frontend')
const backendStaticRoot = path.join(repoRoot, 'backend', 'static')
const assetsRoot = path.join(backendStaticRoot, 'assets')

const esbuildModule = await import(path.join(frontendRoot, 'node_modules', 'esbuild', 'lib', 'main.js'))
const esbuild = esbuildModule.build || esbuildModule.default?.build

if (typeof esbuild !== 'function') {
    throw new Error('esbuild_build_api_unavailable')
}

await fs.mkdir(assetsRoot, { recursive: true })

await esbuild({
    entryPoints: [path.join(frontendRoot, 'src', 'main.jsx')],
    bundle: true,
    format: 'esm',
    platform: 'browser',
    outdir: assetsRoot,
    entryNames: 'app',
    assetNames: '[name]',
    loader: {
        '.css': 'css',
        '.svg': 'file',
        '.png': 'file',
        '.jpg': 'file',
        '.jpeg': 'file',
        '.gif': 'file',
        '.webp': 'file',
        '.woff': 'file',
        '.woff2': 'file'
    },
    publicPath: '/static/assets',
    sourcemap: false,
    minify: true,
    legalComments: 'none',
    logLevel: 'info'
})

const publicRoot = path.join(frontendRoot, 'public')

try {
    const publicEntries = await fs.readdir(publicRoot, { withFileTypes: true })
    await fs.mkdir(backendStaticRoot, { recursive: true })

    for (const entry of publicEntries) {
        const sourcePath = path.join(publicRoot, entry.name)
        const targetPath = path.join(backendStaticRoot, entry.name)

        if (entry.isDirectory()) {
            await fs.cp(sourcePath, targetPath, { recursive: true })
        } else if (entry.isFile()) {
            await fs.copyFile(sourcePath, targetPath)
        }
    }
} catch {
    // No public assets to copy.
}

const template = await fs.readFile(path.join(frontendRoot, 'index.html'), 'utf8')
const html = template.replace(
    '<script type="module" src="/src/main.jsx"></script>',
    '<link rel="stylesheet" href="/static/assets/app.css" />\n    <script type="module" src="/static/assets/app.js"></script>'
)

await fs.writeFile(path.join(backendStaticRoot, 'index.html'), html, 'utf8')
