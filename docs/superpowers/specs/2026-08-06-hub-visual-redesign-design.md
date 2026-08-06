# Campus Agent Hub 深夜智能舱视觉改版设计

## 1. 目标

将当前偏后台管理风格的 Hub 改造成具有明确产品气质的校园 Agent 集成平台。视觉方向采用用户确认的“深夜智能舱”：以深色沉浸空间、中心提问入口和动态星云作为第一视觉信号，同时保留 Agent 广场、统一聊天、开发者接入、管理审核和演示身份切换等完整功能。

本次改版只调整前端信息呈现、视觉系统和局部交互，不改变 Hub 后端、Registry、Gateway、Manifest、认证、治理或 Agent 调用协议。

## 2. 设计原则

- 首屏只保留一个主视觉中心：校园问题输入入口。
- 侧栏承担稳定导航，不与主视觉竞争。
- “瀚海行”作为 Featured Agent 在推荐区拥有最高视觉层级，但不遮蔽第三方 Agent 的可发现性。
- 装饰动画只存在于主工作区背景，不进入表单、审核表格和正文阅读层。
- 深色不是纯黑堆叠：使用中性黑灰为基础，以冷蓝、紫色和少量白色星辉建立空间层次。
- 不通过大量圆角卡片、强渐变或常驻高亮制造“科技感”。

## 3. 页面结构

### 3.1 应用壳

桌面端保持左侧导航和右侧内容区：

- 左侧导航宽度约 220 至 236 像素，使用稳定的深灰表面。
- 品牌区显示校徽、`AI for better Life` 和 `Campus Agent Hub`。
- 一级入口保持现有四项：Agent 广场、最近使用、开发者接入、管理审核；管理审核继续仅对管理员显示。
- 当前演示身份、主题和设置入口位于侧栏底部或右上角，不进入主视觉标题区。

移动端继续使用抽屉式导航，不把桌面侧栏压缩为长期占据内容宽度的窄栏。“发起新任务”是 `/hub` 首屏的视觉标题和主操作，不新增同名路由或重复导航项。

### 3.2 首页首屏

首屏由以下部分构成：

1. 小型品牌标识 `AI FOR BETTER LIFE · USTC`。
2. 主标题“今天，想解决什么校园问题？”。
3. 一句说明用户需要主动选择专业 Agent，避免暗示平台已经实现自动语义路由。
4. 居中的主输入框，支持搜索 Agent、课程或校园服务。
5. 下方推荐 Agent 区，其中“瀚海行”作为 Featured Agent 首位展示。

原有三块统计卡和三排完整筛选不再占据首屏。分类与接入等级筛选收束到 Agent 广场视图或可展开筛选区。

`/hub` 继续是首页与 Agent 广场合一的唯一 Portal 路由，不新增 Home 路由。首屏输入框使用新 ID `#portalSearch`，用途是筛选和发现 Agent，不直接向模型发送消息。静态顶栏的 `#globalSearch` 保持存在并继续用于所有 Portal 搜索。两个输入框共享 `state.query`：任一输入变化时都通过同一个查询更新函数同步另一个输入框并刷新 Agent 列表。`#portalSearch` 每次由 `renderPortalShell()` 重建后重新绑定；`#globalSearch` 仍只在 `init()` 中绑定一次。Portal 页面可以视觉隐藏顶栏搜索框，但不得从 DOM 中删除或复制其 ID。

### 3.3 其他视图

- Agent 广场：保留搜索、分类、接入等级和标签筛选，降低筛选控件的常驻视觉重量。
- Agent 详情：保留能力、接入等级、维护者、数据政策和主操作。
- 统一聊天：使用同一深色壳层，但聊天正文背景保持稳定，星云不得穿透消息阅读区。
- 开发者接入：保持 Manifest 预览、校验和提交闭环，采用高密度工作台布局。
- 管理审核：保持列表、机器验收证据、治理操作和状态反馈，装饰动画默认关闭。

## 4. 动态星云

动态背景实现为独立 ES 模块 `apps/hub/web/starfield.js`。`app.js` 只负责在 Portal 挂载、卸载和传递容器状态，不包含随机分布与粒子更新算法；`hub-core.js` 不承载任何视觉算法。

`starfield.js` 至少导出以下可测试边界：

- `createSeededRandom(seed)`：生成可复现的伪随机序列。
- `sampleGaussianPair(random)`：执行 Box-Muller 变换。
- `generateStarField(options)`：根据宽高、密度和种子生成星点，返回纯数据。
- `createInteractionState(options)`：创建受粒子上限约束的尾迹与星屑状态。
- `stepInteraction(state, input, deltaMs)`：推进交互状态，不访问 DOM。
- `shouldAnimate(mediaReducedMotion, documentVisible, pointerFine)`：决定是否运行持续动画。

Canvas 生命周期、`ResizeObserver`、`visibilitychange` 和指针事件封装在模块内部的 `mountStarfield(container, options)` 中；该函数返回 `destroy()`，路由离开 Portal 或重新渲染前必须清理监听、观察器和动画帧。

### 4.1 分布模型

星点使用 Canvas 生成，不引入 3D 或大型粒子依赖。

- 使用带固定种子的伪随机数生成器，保证同一版本的初始星空可复现。
- 使用 Box-Muller 变换生成二维正态随机数。
- 以标题和输入框之间为中心生成椭圆高斯星团，水平方向方差大于垂直方向。
- 使用低频值噪声对候选星点做接受采样，打破规则的椭圆边界。
- 少量星点使用全局均匀采样，形成外围孤星；左右边缘密度明显低于中心。
- 星点包含冷蓝、蓝紫和低饱和白三类颜色，大小和基础亮度随机，但高亮星只占少数。

### 4.2 常驻运动

- 每颗星使用不同相位和频率做非同步亮度呼吸。
- 星点只进行数像素范围内的椭圆漂移，不能穿过页面形成明显位移。
- 少数亮星以低频率出现短暂十字星芒。
- 动画不得改变布局尺寸，也不得影响输入响应。

### 4.3 鼠标星雾交互

交互无需点击或按住：

- 鼠标进入主工作区后，移动轨迹会在深色雾层中形成逐渐衰减的透明尾迹，呈现“拨开迷雾”的效果。
- 轨迹附近生成短生命周期的蓝紫星屑，停止移动后，指针附近仍可低频生成少量星辉。
- 轨迹采样需要按时间和距离节流，避免高刷新率鼠标造成粒子爆炸。
- 星屑、尾迹和雾团都有数量上限，并及时移除失效对象。
- 主视觉背景禁止文字误选；输入框和文本域仍允许正常选择、输入和复制。
- 交互 Canvas 使用 `pointer-events: none`，不能阻断按钮、链接和输入框。
- `prefers-reduced-motion: reduce` 下停止持续动画，保留静态星空并关闭跟随式星屑。

## 5. 视觉系统

- 页面基础背景：接近 `#0b0c10` 的中性黑。
- 侧栏表面：接近 `#15171c`，通过细边框与主区分离。
- 主文字：低饱和白；辅助文字：中性灰。
- 强调色：冷蓝和蓝紫，仅用于选中态、Featured 边框、品牌标识和星辉。
- 卡片圆角不超过 8 像素；胶囊形只用于标签和明确的状态。
- Agent 卡片具有固定最小高度，动态内容不能导致同排布局跳动。
- 不为本次改版新增图标依赖；导航和操作图标优先复用现有文字、图片或浏览器可访问的标准符号。Agent 自身图标使用 Registry 返回并由 Hub 缓存的资产。
- 字号不随视口宽度线性缩放；使用断点和有限的响应式约束。

## 6. 必须保留的功能契约

以下路由保持不变：

- `/hub`
- `/hub/recent`
- `/hub/submit`
- `/hub/admin`
- `/hub/agents/{id}`
- `/hub/agents/{id}/chat`

以下关键 DOM 和行为契约保持可用：

- `#app`、`#view`、`#globalSearch`、`#identitySelect`、`#userAvatar`、`#themeToggle`
- `.mobile-nav-toggle`、`[data-link]`、`[data-nav]`、`[data-admin-only]`
- `[data-category]`、`[data-level]`、`[data-chip]`
- `.hub-card`、`[data-primary-action]`
- `#composer`、`#prompt`、`#cancelRun`、`#sendRun`、`#messages`

不得改变 `hub-core.js` 中的请求形状、SSE 解析、安全 Markdown 渲染、API 地址和错误归一化逻辑。

## 7. 性能和可访问性

- Canvas 按设备像素比缩放，但最高限制为 2，避免高分屏过度消耗。
- 页面不可见时暂停动画；重新可见时恢复。
- 粒子总数根据视口面积设上限，移动端进一步降低数量。
- Canvas 使用 `aria-hidden="true"`；关键信息不得只通过颜色或动画表达。
- 所有交互控件保留键盘焦点、可访问名称和足够对比度。
- 深色主题下正文与背景对比度至少满足 WCAG AA。
- 移动端不启用鼠标跟随效果，改用静态星空。
- 产品默认采用“深夜智能舱”深色主题；现有主题循环仍保留。用户显式选择浅色主题时关闭动态星云，使用无动画的浅色背景，避免在浅色表面机械反色。

## 8. 验收标准

### 8.1 功能回归

- 广场加载、搜索、分类、接入等级和标签筛选正常。
- Link、Connected 和 Featured 三种 Agent 的主操作正常。
- 统一聊天流式输出、取消、引用和工具调用展示正常。
- 开发者提交、Manifest 预览和服务端验收正常。
- 管理员批准、拒绝、暂停、恢复、废弃和回滚正常。
- 演示身份切换后旧异步响应不会写入新身份界面。

### 8.2 视觉和交互

- 桌面首屏能同时看到主提问入口和至少一行推荐 Agent。
- 星团视觉中心位于标题附近，两侧明显稀疏。
- 鼠标只移动、不点击时即可触发拨雾和星辉。
- 在主视觉区移动鼠标不会选中文字。
- 输入框、按钮、链接和筛选控件不被 Canvas 遮挡。
- 360、768、1280 和 1440 像素宽度下无文字截断、无横向页面滚动、无控件重叠。

### 8.3 自动化与浏览器验证

- 保持现有 Hub Python 和 JavaScript 测试全部通过。
- 在 `apps/hub/tests-js/starfield.test.mjs` 中覆盖随机序列可复现、中心密度高于边缘、星点边界、粒子上限、状态衰减和减少动态效果。
- 本地服务启动命令为 `powershell -ExecutionPolicy Bypass -File deploy/run-demo.ps1`；固定演示入口为 `http://127.0.0.1:8100/hub`。
- Python 回归命令为 `python -m pytest apps/hub/tests -q`。
- JavaScript 回归命令为 `node --test apps/hub/tests-js/*.test.mjs`。
- 固定 Demo 验收命令为 `python deploy/verify_demo.py --iterations 10`；仍以至少 9 次成功为通过。
- 浏览器验收使用管理员 `demo-a`，检查 `/hub`、`/hub/recent`、`/hub/agents/{id}`、`/hub/agents/{id}/chat`、`/hub/submit` 和 `/hub/admin`。桌面视口使用 1440×900 和 1280×800，移动视口使用 390×844。
- Portal 的可观察选择器为 `#portalSearch`、`#agentGrid` 和 `[data-starfield]`；Canvas 挂载元素使用 `[data-starfield-canvas]`，并保持 `aria-hidden="true"`。
- 桌面深色主题下确认 Canvas 像素非空，间隔 500 毫秒的两帧哈希不同；`prefers-reduced-motion: reduce` 或浅色主题下两帧保持稳定。
- 仅移动鼠标、不按键，在标题到输入框区域移动至少 200 像素；确认交互状态产生尾迹与星屑、`window.getSelection().toString()` 为空，并确认 `#portalSearch` 仍可获得焦点和输入文本。
- 每个验收页面读取浏览器控制台，未处理异常数量必须为 0；对首页、聊天页和管理审核页各保留桌面与移动端截图作为本次实现的审计证据，截图不提交仓库。

## 9. 非目标

- 不新增自动 Agent 路由、Main Agent、Agent 间调用或递归委派。
- 不重构 Hub 后端和 Contract。
- 不引入 Three.js、WebGL、物理引擎或远程动画服务。
- 不把星云动画复制到聊天正文、开发者表单或管理审核数据区。
- 不在本次视觉改版中实现真实登录或公网部署。
