# Future Work Demo Agents 设计

## 目标

在不伪装真实数据接入的前提下，为 Agent 广场和首页需求路由增加两个可演示的占位 Agent：评课社区 Agent、校园公共服务 Agent。

## 边界

- 两个 Agent 使用 Contract v1 的 `simple-chat` Connected 接入方式；
- 可以从广场进入 Hub 统一聊天，也可以被首页需求路由推荐；
- Demo 回复使用固定示例内容，并明确标记 `Future Work Demo`；
- 当前不抓取评课社区、不查询真实校内位置、不保存用户帖子或对话；
- 真实数据源、权限和内容审核留到后续迭代。

## 实现

注册两个独立 Manifest，并由 bootstrap 完成提交、契约验收、审核和健康检查。底层复用现有 Demo Agent 进程，通过 Hub JWT 的 `aud` 区分三个 Demo Agent 的固定回答；每个 Registry 项仍保持独立的 Agent ID、能力声明和路由摘要。

路由清单增加课程评价、教师评价、选课建议、签字盖章、行政窗口、楼宇位置和办事经验等关键词。路由后端仍只返回经过静态清单与 active Registry 双重校验的 Agent ID，用户点击后才进入统一聊天。

## 验收

- 四个 Agent 均能注册、通过 Contract v1 验收并出现在广场；
- 两个新 Agent 的固定问题能返回对应领域的 Future Work Demo 回复；
- 首页路由分别命中两个新 Agent；
- 原有瀚海行、校园助手和模型配置流程不回归；
- 浏览器桌面/移动端无布局错误，控制台无新增错误。
