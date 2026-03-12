<div align="center">

# astrbot_plugin_portrayal

人物画像插件。基于群友聊天记录提取文本，再调用 LLM 分析画像或生成人格克隆提示词。

</div>

## 兼容性

- AstrBot `3.4+`
- OneBot 11 / AIOCQHTTP 适配器
- 已适配 `NapCat`
- 已适配 `LLOneBot`

历史消息查询依赖 `get_group_msg_history`。插件会自动兼容以下差异：

- `reverseOrder=True`
- `reverse_order=True`
- 顶层 `messages`
- `data.messages`

## 安装

可以直接在 AstrBot 插件市场搜索 `astrbot_plugin_portrayal` 安装。

也可以手动克隆到插件目录：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/Zhalslar/astrbot_plugin_portrayal
```

## 配置

在 AstrBot 面板中进入：

`插件管理 -> astrbot_plugin_portrayal -> 操作 -> 插件配置`

## 使用

- `画像 @群友`
  分析指定群友的人物画像
- `画像 @群友 <查询轮数>`
  指定历史消息轮数，每轮最多拉取 200 条群消息
- `查看画像 @群友`
  查看本地已保存的画像
- `切换人格 @群友`
  将当前会话切换为该群友的克隆人格

插件还支持读取 `builtin_prompts.yaml` 中的画像提示词入口，首个命令词会自动匹配对应画像模板。

## 说明

- 消息只提取文本段，非文本消息会被自动跳过
- 群消息会按群维度扫描，并按用户维度做短期缓存
- 当协议端不返回可识别的历史消息结构时，插件会直接提示兼容问题，而不是静默返回空结果
