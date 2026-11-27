# 短剧分镜剪辑插件市场

本仓库准备发布 `drama-episode-suite`。插件内有三个独立 Skill：`drama-episode-controller`、`lyy`、`jianying-rough-cut`。仓库根目录的 `.agents/plugins/marketplace.json` 指向 `plugins/drama-episode-suite/`。保留这两个名称，使用者以后才能从同一市场更新，而不用反复接收 ZIP。

## 发布者：首次上线

1. 在 GitHub 建一个空仓库。公开仓库便于外部使用；私有仓库要求每个使用者有访问权限。不要把剧本、音视频素材、剧梦令牌或剪映工程放进这个仓库。
2. 把本目录的全部内容放在仓库根目录，包括隐藏的 `.agents` 目录。可从本目录打开 PowerShell，运行以下命令，把 `OWNER` 和 `REPO` 改成实际名称：

   ```powershell
   git init -b main
   git add .
   git commit -m "Publish drama episode suite"
   git remote add origin https://github.com/OWNER/REPO.git
   git push -u origin main
   ```

3. 用另一台有仓库访问权限的电脑试安装，核对插件市场、三个 Skill 和剪映运行环境后再分享仓库地址。

## 使用者：只需添加一次

在 Codex CLI 中运行：

```powershell
codex plugin marketplace add OWNER/REPO
codex plugin add drama-episode-suite@drama-episode-marketplace
```

也可以添加市场后在 Codex 插件目录中安装 `drama-episode-suite`。已有旧的 Personal 版 `lyy`、剪映粗剪或本地 ZIP 合并包时，先备份并停用旧入口，避免同时加载两个版本。插件安装**不会自动运行** `tools/install-runtime.ps1`；首次使用前让 Codex 定位已安装插件，检查剪映专用 Python 与 FFmpeg，缺少时运行这个脚本。剧梦仍需 Dreama CLI 登录和项目权限；剪映、字体及素材也需在本机就绪。

## 发布者：以后更新

1. 修改 `plugins/drama-episode-suite/` 内相关 Skill 或脚本；保持插件 `name` 和市场 `name` 不变。
2. 调高 `.codex-plugin/plugin.json` 的 `version`。运行 `powershell -ExecutionPolicy Bypass -File .\tools\refresh-checksums.ps1`，通过校验后提交并推送。
3. 如果剪映 `requirements.txt` 或 FFmpeg 要求改变，在更新说明中标出；使用者要重新运行 `tools/install-runtime.ps1`。只更新 Skill 指令或脚本时，无需重新下载 ZIP。

## 使用者：以后更新

```powershell
codex plugin marketplace upgrade drama-episode-marketplace
```

然后重开 Codex，核对插件版本及三个 Skill。若已安装的缓存仍显示旧版，在插件管理器刷新或从同一市场重新安装插件；无需重新下载 ZIP。Git 市场更新不是静默更新，不能把“仓库已推送”直接当成每位使用者已切换版本。
