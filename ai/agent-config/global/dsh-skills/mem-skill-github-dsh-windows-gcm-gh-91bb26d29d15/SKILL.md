---
name: mem-skill-github-dsh-windows-gcm-gh-91bb26d29d15
description: Пуш в GitHub из песочницы DSH на Windows (обход GCM-промпта через gh-токен)
---

# Пуш в GitHub из песочницы DSH на Windows (обход GCM-промпта через gh-токен)

> **⚠️ 使用约束（必读）**
>
> 本技能由 **dsh-auto-memory** 从一次真实工作过程**自动沉淀**而来，**不是通用最佳实践**。
>
> 1. **附带程序仅供参考**：本目录下的脚本（如 `.py`）是**当时那次工作用过的程序**，
>    **不是可直接调用的工具**。它们可能依赖当时的路径、环境变量、数据格式或版本。
> 2. **场景根本不同时 → 只做迁移，不要直接运行**：若当前任务与下方「来源」描述的场景
>    **有根本差异**，请把附带的程序**当作思路参考**，按当前场景**重写**，而不是直接执行。
> 3. **跨项目使用须先核对**：本技能的「适用项目」若与当前项目**不一致**，
>    **一律只作参考**，不得直接套用其路径、命令与判据。
> 4. **高风险步骤需人工确认**：涉及删除、发布、付费、外部发送等动作，必须先向用户复述确认。

## 来源

- **适用项目**：`game`（`D:\IDEA 2026\IntelliJ IDEA 2024.3.6\projects\game`）
- **沉淀自**：dsh-auto-memory 技能库（procedure `proc_pre_91bb26d29d154e05369820cd1a27dcef`）
- **风险等级**：`low`
- **导出时间**：2026-10-11T06:41:24.368Z

> 若你的当前项目**不是** `game`，请**只作参考**，不要直接套用下方路径与命令。

## 何时适用

- git-remote на github.com по https; gh залогинен активным аккаунтом со scope repo; интерактивный промпт недоступен (песочница DSH)

## 步骤

1. git-remote проверить: git remote -v (https://github.com/...). 2. Убедиться, что gh активен: gh auth status (нужный аккаунт — Active account: true). 3. Взять токен в переменную, не печатая: $t = (gh auth token).Trim(). 4. Собрать заголовок: $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes('x-access-token:' + $t)). 5. Пуш одним вызовом: git -c ('http.extraheader=Authorization: Basic ' + $b64) push origin main; проверить $LASTEXITCODE.

## 检查点

- [ ] gh auth status показывает активный аккаунт с scope repo; токен не выводится в stdout; повторный пуш той же сессии — тем же способом (креды git не кэширует)

## 成功判据

- push завершается exit 0 с выводом "main -> main" без запроса Username и без запуска sh-промпта

## 回滚

- Не требуется: extraheader передаётся разово через -c и не сохраняется в конфиг


<!-- mem-skill-export-pre-v1 -->
