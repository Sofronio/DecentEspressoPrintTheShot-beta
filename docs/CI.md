# CI 构建与发布说明

本项目的三平台构建由 GitHub Actions 自动完成。公开仓库的 Actions **完全免费**。

## 触发方式

- 推送 `v*` tag(如 `v2.0-beta.1`)→ 自动构建三平台并发布到 Releases
- 手动触发:Actions 页面 → Build PrintTheShot Beta → Run workflow

```bash
git tag v2.0-beta.2 && git push origin v2.0-beta.2
```

## 流水线结构(`.github/workflows/build.yml`)

```
build (matrix: ubuntu/macos/windows)
  ├─ checkout + setup-python 3.11
  ├─ 执行对应平台的 scripts/build_*.sh / build_windows.bat
  └─ 上传产物 artifact(缺失即失败)
release (needs: build, 仅 tag 触发)
  ├─ 下载三个平台的 artifact
  ├─ 显式重命名:
  │    PrintTheShot-linux          (Linux 裸二进制)
  │    PrintTheShot-macos.zip      (macOS .app 压缩包)
  │    PrintTheShot-windows-x64.exe(Windows)
  └─ 发布到 GitHub Release
```

## 踩坑记录(重要)

### 1. Windows:裸 `pip` 在 venv 中自升级会报错

**现象**:`pip install --upgrade pip` 报 `ERROR: To modify pip, please run the following command: ... python -m pip`。

**原因**:Windows 上 venv 的 `pip.exe` 无法修改自身(文件占用)。

**修复**:bat 中一律使用 `python -m pip install ...`。

### 2. Windows:bat 没有错误检查 → "假成功"

**现象**:pip 安装失败后 bat 继续执行,最后一条 `echo` 成功退出码 0,整个 job 显示 ✓,但产物为空。

**修复**:每步 `if errorlevel 1 exit /b 1`,最后校验产物存在:

```bat
python -m pip install --quiet -r scripts\requirements.txt pyinstaller
if errorlevel 1 exit /b 1
python -m PyInstaller scripts\print_the_shot.spec --noconfirm
if errorlevel 1 exit /b 1
if not exist dist\PrintTheShot.exe (echo ERROR & exit /b 1)
```

> 通用原则:构建脚本必须让失败**显式暴露**,不能静默返回 0。

### 3. Release:多个 job 发布同名文件互相覆盖

**现象**:三个平台的构建产物都叫 `PrintTheShot`(PyInstaller 默认名),每个 job 各自调用 `softprops/action-gh-release` 发布,先到的被后到的覆盖,Release 只剩一个文件。

**修复**:构建 job **不做发布**;单独一个 `release` job 依赖所有构建,下载 artifact 后**显式重命名**再发布(见流水线结构)。

### 4. Release:.app 目录结构被拍平,漏出 `Info.plist`

**现象**:`download-artifact` 加 `merge-multiple: true` 后,`.app` 包里的 `Contents/Info.plist`、`MacOS/PrintTheShot` 被当作独立资产发布。

**修复**:
- macOS 构建脚本把 `.app` 打成 zip(`zip -rq PrintTheShot-macos.zip PrintTheShot.app`)——这也是 macOS 应用分发的标准形态
- release job 不用 `merge-multiple`,按 artifact 目录精确拷贝

### 5. 上传产物用 `if-no-files-found: error`

产物缺失说明构建失败,必须让 job 标红,而不是 `ignore` 静默跳过。

## 本地构建(调试用)

| 平台 | 命令 | 产物 |
|---|---|---|
| macOS | `./scripts/build_macos.sh` | `dist/PrintTheShot` + `dist/PrintTheShot.app` + `dist/PrintTheShot-macos.zip` |
| Linux | `./scripts/build_linux.sh` | `dist/PrintTheShot` |
| Windows | `scripts\build_windows.bat` | `dist\PrintTheShot.exe` |

注意事项:

- PyInstaller **不能交叉编译**:必须在本平台构建(CI 的 matrix 天然满足)
- spec(`scripts/print_the_shot.spec`)把 `fonts/` `web/` `plugin/` 作为 datas 打进包;这些目录**必须提交到 git**,否则 CI 构建缺文件
- 打包版运行时从 `_MEIPASS` 读取字体/模板;插件会复制到 CWD/plugin(可写,支持更新)

## 发布后验证清单

1. Actions 页面:3 个 build + 1 个 release 全部 ✓
2. Release 资产恰好 3 个,命名正确(linux / macos.zip / windows.exe)
3. 抽查:下载对应平台包,运行 `--render sample.json out.png` 验证图表渲染
4. macOS 包解压后 `PrintTheShot.app` 可双击启动
