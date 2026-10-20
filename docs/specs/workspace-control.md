# 工作空间控制协议 — 首个项目元数据切片

> 当前状态更新（2026-10-21）：本规格对应能力已在S08–S18相应阶段及S18统一版本中验收。下文旧阶段状态为实现历史，当前边界与交付核验见[统一版本说明](../user-guide/unified-delivery.md)。S19最终交付须以新源码冻结和新CI为准。


实现状态：历史增量如下；S04已新增正式产品任务与确切输入绑定/只读运行前置，当前协议见product-tasks.md。其余领域、任务执行/写回及五方向整体尚未完成。

## 契约
ProjectRevision：schema_version=1、project_id匹配p-[0-9a-f]{16}、revision正整数、name非空白且≤100、description≤2000、archived布尔。禁止额外字段和运行时修改，不包含任意宿主路径/客户端tenant/actor。

workspace.resource_committed载荷：schema_version=1、resource_kind=project/source_grant/source_snapshot（各有严格子模型）、resource_id、revision、parent_revision、blob_ref、digest、command_id、request_digest、actor_ref。blob_ref=digest均为64位小写hex，revision=parent_revision+1，命令键为1–128安全字符。source_snapshot身份为ss-16hex且仅r1/p0。事件强同步。当前生产词表83类型/强同步16类型，唯一权威仍events/vocab.py，历史文档中的旧计数不作为当前断言。

## 端点
GET /api/v1/projects：受权项目列表与精确total，上限200项目。POST同路径：严格body包含project_id、expected_revision、name、可选description/archived；Idempotency-Key必填；相同主体同key同body返回历史结果，不同body409；CAS旧版本409。创建只写元数据，不扫描或授权目录，不提交任务。归档是新revision，不删除历史。
GET /api/v1/workspace/audit：无查询参数时保留旧版全量events/total兼容响应。显式pagination_version=1启用有界响应：limit默认50、范围1–200；可选project_id（仅当前已实现project）、cursor。重复/未知参数、无版本的分页参数、非法数字或未知版本422。不得将分页模式的精确total解释为页长度。

分页DTO：schema_version=1；events只含seq/type/ts/resource_kind/resource_id/revision/digest（session.created的资源字段为null）；total为固定水位内过滤后精确条数；page_count为本页条数；has_more、next_cursor（末页null）；source_watermark={seq,digest}。无过滤包含session.created，project_id过滤不包含该首事件；空匹配返回total=page_count=0、events=[]、has_more=false。顺序按seq，不使用offset；新提交不改变旧游标水位，刷新无cursor建立新水位。

游标是HMAC-SHA256完整性保护的规范化base64url token，不是加密或授权凭证。绑定版本1、租户/主体的域分离HMAC-SHA256摘要（不暴露可离线字典核对的裸身份hash）、project_id、limit、固定seq与完整事件前缀SHA256、after seq、初始签发时间；后续翻页不延长一小时有效期。严格类型/未知字段/签名/规范编码/身份/过滤/范围/前缀摘要/期限校验；只接受过滤内非末事件after。游标不含原始tenant/subject或私有路径。密钥由服务端生成并存于同租户私有workspace-v1/.audit-cursor-key，沿已有OS owner lease串行保护；权限继承私有存储根，不向API/UI/日志/证据包公开。备份含此密钥需按凭据处理；遗失或轮换密钥使旧游标无效，绝不凭旧token自授权限。每次请求先重新鉴权，账户撤销后旧游标仍拒绝。

当前每页仍完整重放并校验磁盘控制日志与所有引用blob，再返回至多200事件；只有响应有界，存储读取/CPU/内存不有界，未验证大容量性能或实现SQLite索引。日志/摘要损坏继续fail closed且不repair；前缀替换使旧水位拒绝。无新领域事件类型，不调用引擎/Provider/队列。

错误沿已注册稳定码：EVT-100字段/幂等冲突，EVT-101版本冲突，PERS-202写锁/存储/摘要/损坏/提交未确认，EVT-104关闭拒绝，GRD-403权限拒绝，CRED-701身份缺失。HTTP映射401/403/409/422/503，响应只给安全reason，不泄露原值或路径。既有platform错误兼容不改。

## 身份和生命周期
租户来自ApplicationService；账户主体来自服务端current_actor。账户管理租户必须有成员资格，viewer不可写，owner/member可写；无账户管理的本地通道标local-operator，不伪造独立身份。每次请求复核权限，幂等不能绕鉴权。关闭由ApplicationService负责，读取历史不启动引擎、模型、MCP或队列。

## 持久化和故障
ADR-038规定SessionLog唯一提交事实。blob先于事件，孤儿不是已创建资源；强同步后必须准确读回事件。日志身份/seq不连续、坏行/残行、blob缺失或hash变化拒绝，不自动repair/delete。append失败后owner fail closed直到重开。不保证跨远端exactly-once，本切片没有外部效果。

## 界面
Web左侧“项目空间”或/#projects；保存/编辑/归档/取消归档/版本摘要/控制seq。页面“控制审计”查看全部控制事实，每项目“项目审计”过滤该ID；只读dialog提供上一页/下一页/刷新水位，展示seq/type/resource/revision/digest、精确总数与固定水位；加载/空/权限/失败/过期状态不造成功。ESC或关闭返回触发按钮，关闭/换页失效旧异步响应。所有用户文本esc或textContent输出；未知提交保留同key同body核对重试，不用新键自动重发。旧入口保留。只读目录授权见下节；快照/资料/任务关联/原生项目页尚未交付，页面明确提示。

## 已有验证
tests/unit/test_workspace_contracts.py、test_workspace_store.py、test_workspace_adversarial.py、test_workspace_projects.py，以及扩展test_events.py。真实独立进程写锁、两租户同ID、CAS竞态、幂等冲突、磁盘错误/吞异常、损坏保真、HTTP负例和真实Chrome操作。最终执行数字以任务外JUnit及报告为准。

## t_6321c198：只读 SourceGrant 子切片

GET/POST /api/v1/projects/{project_id}/source-grants；POST /api/v1/source-grants/{grant_id}/revoke。写需Idempotency-Key；签发body只接受schema_version=1、grant_id=sg-[0-9a-f]{16}、expected_revision=0、source_path绝对本地目录、actions=['read']、未来UTC expires_at；撤销仅schema_version及expected_revision>=1。禁止bool充当版本/整数、额外主体/tenant/identity、非read动作。签发只建r1；撤销是不可逆新revision；重新授权必须新ID。账户团队只有owner可管理授权，member/viewer可读但不能扩大根；本地可信操作者通道标local-operator。所有请求每次重新鉴权，幂等不替代授权。

目录安全边界：先规范Windows分隔符，拒相对/drive-relative/UNC/设备/URI/NUL/点段，复用core.sandbox.safe_path，以本地卷anchor与relative components检查所有祖先，拒保留名/ADS/尾点空格/symlink/junction，再resolve(strict=True)+stat确认目录且非卷根。不列文件、不读源正文。root_identity由服务端SHA256(canonical({normalized_root:os.path.normcase(resolved),device:st_dev,inode:st_ino}))派生，非零inode；不得信任客户端自报。私有不可变SourceGrant blob保存canonical_root/device/inode/project_id/approved_by等；日志只保提交摘要，不写路径。使用同租户workspace-v1/blobs/<sha256>.json和同一控制SessionLog，不再造权威文件。先blob再强同步事件再磁盘确认，原owner lease/首事件约束不变。最大200授权/项目，历史撤销记录计入，未经授权不清理。

API摘要（签发/撤销/列表项）仅grant_id/revision/root_identity/actions/expires_at/revoked/digest/source_seq；不公开private root、device、inode、approved_by或原请求。原始路径仅显式签发请求与私有blob持有，不进日志/错误/审计/导出。账户和控制数据目录依赖现有OS私有存储权限，不承诺对管理员保密；私有blob不得纳入公共证据包。

读取摘要只重放控制日志/blob，零源目录stat/引擎/Provider/队列。签发与撤销前同样从磁盘重查完整日志/blob/seq/缓存一致性，坏行/残行/摘要变化fail closed且不repair。重放严格区分project/source_grant；grant父项目须已存在，签发人由事件主体核对，后续只允许保持所有绑定的撤销转换。历史project投影输出不混入grant。旧audit游标签名/水位语义保持，刷新全审计可显示source_grant；project_id过滤仍仅该project资源历史，不扩为全部子资源。

内部require_source_grant只验证当前未撤销、UTC未过期、父项目未归档的状态并返回安全摘要；不返回执行token、不读取文件，也不取代服务鉴权或工具guard。撤销立即拒绝新的状态准入，但本片没有源文件访问API，因此不是完整授权执行链。目录身份只是签发时metadata观察，不能防OS级恶意竞态；未来正文读取/快照必须重新核对身份、根边界、最新授权和既有guard。

Web /#projects → 每项目“只读源授权”：可显式输入目录和本地到期时间（转UTC）、查看摘要/过期/撤销、刷新、核对同key原命令。成功清空路径输入，不写浏览器持久存储；5xx/网络未知不自动换key重发。dialog提供加载/空/权限/冲突/失败、ESC/关闭返回焦点，过时响应不更新页面。原生新页、正文导入、快照、任务准入/回写均不属于本片。验证入口tests/unit/test_source_grants.py，真实Chrome/HTTP/独立进程与全量结果以本卡证据为准。

## t_6d3fc94b：非持久目录身份 metadata 预检

POST /api/v1/projects/{project_id}/source-grants/{grant_id}/preflight；body恰有schema_version（必填严格int=1）和expected_revision（必填严格正整数）。拒绝额外路径/identity/actor/tenant/actions和bool版本；不需Idempotency-Key、不创建控制命令/事件/blob。没有控制日志或空日志直接拒绝，不初始化session.created。既有日志重开仍复用单写owner lease，metadata检查覆盖同一async锁；不是OS级原子快照。

每次从服务端派生身份和当前成员资格，owner/member/viewer均可对已有授权预检，但成员不得签发/扩根。账户中间件仅精确放行这一只读POST（严格project/grant ID形状），其余POST/跨站/租户检查不放宽。先重放完整磁盘日志、校验所有blob、确认缓存一致，再检查grant父项目绑定、未归档、CAS、未撤销和UTC未过期；旧expected_revision返回409，其余不可用授权403；损坏日志/blob为503并原字节保留。预检不repair或清理。

从私有blob取canonical_root，复用authorize_root→core.sandbox.safe_path逐目录/祖先检查、resolve/stat并与服务器root_identity比对。不列源目录或读取正文；缺失/非目录/链接返回GRD-403 source_root_unavailable，metadata身份变化返回GRD-403 source_root_identity_changed；无私有路径/设备/inode/主体进入响应或错误。成功恰返回schema_version=1、project_id、grant_id、revision、root_identity、checked_at(UTC)、status=metadata_match、digest、source_seq。仅这一次metadata观察通过，不是执行token、文件读取权限或SourceSnapshot授权；未来真实访问仍须重查授权/identity/工具guard，不能消除OS TOCTOU。

Web同一授权dialog每条未撤销授权有“检查目录身份”，展示加载/安全摘要/拒绝/冲突/身份失效/503/未知失败；新检查、授权变更、刷新或关闭使旧异步结果失效，失败不保留metadata_match。ESC/焦点沿原dialog。测试test_source_preflight.py含真实HTTP→服务→SessionLog与metadata链、重启、损坏、成员撤销、两租户同ID、目录替换、真实Windows junction及权限受限symlink；执行数字与限制见任务外证据，不将skip记为通过。

## t_7c8340aa：显式文件选择的有界 metadata 预览

POST /api/v1/projects/{project_id}/source-grants/{grant_id}/selection-previews。body恰有schema_version（必填strict int=1）、expected_revision（strict正整数）、paths（1–200个strict字符串，每个1–1024字符）。不接受客户端根/身份/额外字段；不需幂等键、不持久化预览、不签发token。复用与目录预检相同的每请求身份、read权限、父绑定、CAS、撤销/过期/归档、磁盘日志及所有blob一致性、owner lease及同async锁和根identity检查。账户中间件仅新增该精确ID形状只读POST例外，其他写权限不放宽。

只检查操作者逐项显式提交的相对文件名；不枚举目录、不Git扫描、不读取正文、不复制快照/启动执行。safe_path复用于每项并检查全部祖先；拒绝绝对/UNC/反斜杠/点段/空组件/ADS/设备名/尾点空格/控制字符、symlink与junction。凭据类组件（.env*、.git、SSH/云凭据/个人浏览器profile、凭据/token/secret/API key常见名及私钥扩展名）在源文件metadata之前排除；不是内容秘密检测器，正常显式.ai-coding规则文件可观察。仅普通文件，每项及累计≤10MiB；后续重复/大小写碰撞项排除，超预算项不纳入累计，可继续观察后面的有界小文件。不打开特殊文件。路径安全拒绝不回显原值。

响应恰schema_version/project_id/grant_id/revision/checked_at(UTC)/status=metadata_observed/items/summary/digest/source_seq。items按输入顺序，accepted项含input_index/status/path（原安全相对名）/size_bytes/mtime_ns（精确十进制字符串，避免浏览器JSON整数精度损失）；excluded项只有input_index/status/reason，禁止回显非法绝对根、凭据名或完整私有路径。reason为path_rejected/sensitive_name/duplicate_path/file_unavailable/not_regular/file_too_large/selection_size_limit。summary含requested/accepted/excluded/total_size_bytes，计数按真实项计算。空accepted不代表访问授权通过。无device/inode/主体/根路径输出。

Web /#projects → 只读源授权 → 每条授权中每行输入一个相对文件名 → “只读预览所选文件metadata”。不自动修复/trim用户路径，不写浏览器持久存储，关闭清空选择。加载/401/403/409/503/未知失败均清除旧成功态；输入变更/刷新/其他检查或授权写/关闭失效旧响应；ESC返回项目按钮。输出仅textContent，明确未读取正文/未枚举目录/未创建快照，非执行授权。metadata不是原子快照、没有内容hash，不消除OS TOCTOU或身份ID复用；后续读取/快照须重验权限及guard。完整P03 dirty快照和其余26需求不因此标为完成。验证见test_source_selection.py及本卡任务外真实HTTP/Chrome/全量JUnit证据。

输入变更只使metadata观察（预检/所选文件预览）的成功及失败响应过时，不取消同一打开dialog内签发/撤销命令的完成核对；成功仍读取授权摘要并显示确认，失败仍显示拒绝或未确认，不残留永久提交中。关闭/重开/换项目保留原dialog生命周期隔离，不允许旧命令响应覆盖新界面。竞态回归：tests/browser/source-grants-race.test.cjs（真实Chrome，API门控夹具为合成数据）。

## t_e295c357：显式选择 UTF-8 文本私有快照

R1授权时序：最终源稳定性复读之后重新从磁盘控制事实核验同grant/父绑定/CAS/归档/撤销及账户角色；根metadata IO完成后以可信UTC检查到期，随后才准入提交。预览也须在读取完成、成功返回前重验；不得靠owner锁冻结时间或删除稳定性复读缩短窗口。到期拒绝不追加快照commit，先写孤儿不算成功且不自动删除。

R1界面观察：目录预检、metadata、快照预览/列表、输入变更、刷新、关闭和显式失效统一清除所有行被替代的摘要/loading、WeakMap预览及checkbox；旧响应不恢复确认。该观察版本与持久命令生命周期分离，不能取消正在进行的原key/body及精确读回。

桌面接手补充：同一打开dialog内刷新授权摘要也不能使正在捕获的POST/精确GET完成核对失效。观察请求仍按generation隔离；持久命令只按dialog生命周期判断能否更新当前界面，关闭/重开/换项目或宿主显式invalidate后不得覆盖新dialog。成功精确读回后显示持久确认，未知结果仍保留原key/body供显式核对；不得留下已清pending却无法操作的“未确认”状态。回归见tests/browser/source-snapshot-refresh.test.cjs（真实Chrome、合成API门控）。

这是选择性副本，不是Git快照、完整dirty工作区、OS隔离或原子文件系统快照。只读操作者逐项选择的1–200文件；不枚举目录、不Git扫描、不执行、不回写源。单文件及总字节≤10MiB，每grant最多200历史快照。所有准入重新验证身份、当前角色、真实SourceGrant、父项目绑定、CAS、归档、撤销、UTC到期及根identity；取得owner锁后再次鉴权。owner/member及本地操作者可用，viewer不能读取正文预览或创建副本；旧metadata响应不是授权token。

POST /api/v1/projects/{project_id}/source-grants/{grant_id}/snapshot-previews，body沿SelectionPreview，返回schema_version/project_id/grant_id/revision/status=confirmation_required/policy/confirmation_digest/file_count/total_size_bytes。预览读取但不持久化正文；不返回正文、原路径清单、device/inode或私有根。POST同级snapshots额外要求snapshot_id=ss-16hex、confirmation_digest=64hex、policy=reject-secrets-utf8-v1及Idempotency-Key。捕获重新读取，必须与确认摘要相等；同主体同key同body返回旧结果，但仍重验当前授权与磁盘完整性，不绕过撤销。不同body冲突。GET snapshots返回snapshots/精确total安全摘要，仍验证当前grant与角色，不提供公开正文下载。

reject-secrets-utf8-v1：严格UTF-8，保留BOM/CRLF原字节；拒NUL、TAB/CR/LF以外C0控制字符、无法解码的编码及二进制，继承凭据文件名排除，名称/正文应用既有redact与有限保守模式（私钥、常见服务密钥、JWT/Bearer、password/token/key/secret赋值、带凭据URL）。任一命中整次拒绝，安全reason不回显原值；不自动改写或脱敏后保存。不解析PDF/Office/图片/压缩。有限模式不等于完整DLP，不保证发现任意自然语言或未知格式秘密；操作者须确认有权复制且无秘密，本卡验证只用合成夹具。

安全读取沿safe_path验证祖先/链接/路径；只开普通文件，拒多hardlink，descriptor前后fstat及路径lstat比对device/inode/size/mtime/mode，并比对读取长度。整组选中读完及提交前再次读回并校验内容hash和根identity。源变化409，不自动重试。可观察稳定性标stable_observed，不承诺阻止恶意OS级TOCTOU或最后检查后的瞬间改写。

私有不可变manifest为SourceSnapshot（strict/extra=forbid）：snapshot/project/grant身份、grant_revision、root_identity、r1、kind=explicit_selection、policy、capture_stability、confirmation_digest、total_size_bytes及files。每文件path/size_bytes/sha256/mtime_ns十进制字符串/mode/device/inode；清单唯一、累计值和确认摘要必须一致。租户workspace-v1/snapshot-content/<sha256>.bin保存原字节，既有blobs/<digest>.json保存manifest；manifest仅此类型上限512KiB，旧类型仍32KiB。全部内容策略通过后才写blob→manifest→同一SessionLog强同步workspace.resource_committed→精确磁盘提交/引用/内容读回。无commit孤儿不算成功且不自动清理；损坏/缺失内容拒绝，append未确认使owner失败关闭。重启重放检查父grant与project绑定、revision和所有内容摘要；不新增权威日志或事件类型。私有存储仍依赖现有OS权限，不对管理员提供保密保证，不能打入公开证据包。

Web项目空间→只读源授权→逐项相对文件名→“安全读取并预览快照摘要”→勾选人工确认→“确认创建私有快照”；未预览/未勾选不能发送创建。成功后GET精确核对snapshot_id/digest/source_seq再显示持久确认。输入变更清空确认、失败清空旧预览；刷新/关闭/换项目使旧结果失效；ESC恢复触发焦点，重开不保留确认。摘要仅textContent。未知提交保留同key原body核对，不自动重发。tests/unit/test_source_snapshot.py、test_snapshot_adversarial.py及tests/browser/source-snapshot.test.cjs覆盖该片；实际全量/覆盖率/Chrome/权限skip以任务证据为准。独立审查前不标DONE，A01/C03和26需求/P01–P19整体仍PARTIAL。

## P03 增量：已提交快照的只读上下文清单预览

POST /api/v1/projects/{project_id}/source-grants/{grant_id}/snapshot-context-previews，严格body为schema_version=1、expected_revision、snapshot_id、snapshot_digest。无需幂等键，不创建资源/blob/事件，不扫描Git、枚举宿主目录或打开宿主文件正文。只从已提交且完整性验证通过的SourceSnapshot派生；同grant/父项目/CAS/当前角色/归档/撤销/根identity/UTC到期均重验，私有manifest/content I/O完成后再次复查。local/owner/member可用，viewer不能通过旧metadata例外获取文件清单；不存在/跨绑定403，摘要或版本冲突409，坏输入422，损坏503。

响应恰schema_version/project_id/grant_id/grant_revision/snapshot_id/snapshot_revision/snapshot_digest/source_seq/policy/status=context_observed/items/file_count/total_size_bytes/context_digest。items保持原快照有序清单，每项仅input_index/path/size_bytes/content_digest；不返正文、绝对根、device/inode/mode或主体。沿原快照1–200文件/10MiB上限；context_digest是除自身以外完整规范响应的SHA256，同一不可变快照重复预览稳定。宿主文件后续编辑不会冒充快照字节，此预览也不证明宿主当前内容新鲜。

Web“读取已有快照摘要”后，每个合法快照可显式“预览快照上下文清单”。清单全部textContent呈现；沿统一观察版本失效，输入/其他观察/刷新/关闭/跨行使旧结果与确认失效，不取消持久命令原键核对。这里只是ContextManifest的有界非持久前置预览，未持久绑定项目或任务、未注入模型、不是实际出站上下文或授权token。完整Git dirty/context manifest/执行及其余26需求仍PARTIAL。回归入口test_snapshot_context_preview.py和source-snapshot-context.test.cjs；最终验收以该切片新冻结证据及独立复审为准。

## P03 增量：持久 ContextManifest 与版本引用接缝

持久路径独立于上述纯预览。`ApplicationService.projects` 沿同一 `WorkspaceStore` owner lease、不可变摘要 blob 和强同步 `workspace.resource_committed` 提交；SessionLog 是唯一控制事实源，投影由完整日志和受验证 blob 重建。没有第二数据库、队列或模型调用。

API（均须当前 local/owner/member，viewer 不能通过 metadata 读取例外访问）：

- GET/POST `/api/v1/projects/{project_id}/context-task-references`；POST 需 Idempotency-Key，严格 body 为 schema_version=1、product_task_id（pt-16hex）、expected_revision、project_revision、title、retired。
- GET `/api/v1/projects/{project_id}/context-task-references/{task_id}/revisions/{revision}` 精确历史读回。退役后旧版本拒读，当前退役版本仍可读回退役收据；不能复活或执行。
- GET/POST `/api/v1/projects/{project_id}/context-manifests`；POST 需 Idempotency-Key，严格 body 为 schema_version=1、manifest_id（cm-16hex）、expected_revision、project_revision、grant_id、grant_revision、snapshot_id、snapshot_digest、task_ref（null 或 product_task_id/revision/digest/source_seq）。
- GET `/api/v1/projects/{project_id}/context-manifests/{manifest_id}/revisions/{revision}` 返回精确历史版本及派生 stale 状态。列表仅返回最新且仍可授权访问的摘要，不含 items。

`TaskContextReference` 是服务端真实持久的最小目标引用，mode=context_reference_only、executable=false；不是完整 ProductTask/RunAttempt/收件箱，也不提供执行准入。创建主体固定在私有定义，跨主体（包括同团队 owner）、跨项目或跨租户不可读写/引用。它绑定确切项目 revision；后续 P05 必须显式消费该接缝，不得把它当作可执行任务。

`ContextManifest` 单向引用确切 ProjectRevision、可选 TaskContextReference revision/digest/source_seq，以及已提交 SourceSnapshot 的 revision=1/digest/source_seq/grant_revision；items 仅 input_index/path/size_bytes/content_digest，无正文、宿主绝对根或主体字段。服务端从快照派生 items，客户端不能自报内容。新版本不改变原 manifest 的父项目、创建主体或 task 身份（含 null），不自动更新项目默认指针或任何执行输入。项目或任务升版、manifest 被更高 revision 替代会派生 stale；旧记录仍可受权查看摘要，但 items=[]，不可作为当前上下文。归档、grant 撤销/到期/根身份失效或任务退役会拒绝相关 manifest 精确 GET 和幂等收据；列表不泄露撤回来源。无删除用户数据 API；逻辑退役/撤权均保留历史事实。

每项目每类最多200次历史提交（按所有ID的 revision 总和计数，不是仅最新ID数）；每清单1–200项、累计10MiB；新清单 blob 最多512KiB。TaskReference及原类型保留32KiB，SourceSnapshot沿原512KiB限额。blob 写后精确读回，最终磁盘/根metadata I/O后重验当前角色，再在账户 I/O 后复查UTC；列表在全部行 I/O 后统一检查已返回授权。提交回执须磁盘日志与事件完全一致；失败 owner 被毒化，关闭重开后以原 key/body 核对，未知结果不自动重放。

Web 在“读取已有快照摘要”之后增加“持久绑定快照上下文”。可保存项目清单、创建/升版/退役仅上下文目标引用、读取和显式刷新旧清单。摘要及名称全部 textContent/Option。POST→精确历史 GET 一致后才显示持久确认；编辑/刷新使观察失效但不丢命令完成状态，关闭重开隔离旧响应。未知命令绑定完整原来源并保留原 key/body；首次明确未接受的401/403/409/422允许纠正，已接受POST后读回失败、网络/503未知以及未知后的重试拒绝均保留原命令。没有自动重发、执行或宿主回写。

实现与验收入口：context_manifest.py、context_manifest_store.py、context-manifests.js、test_context_manifests.py、test_context_manifest_boundaries.py、test_context_manifest_permissions.py、test_context_manifest_recovery.py、context-manifests.test.cjs；真实HTTP/磁盘/Chrome/独立进程证据在 `.ai-coding/evidence/desktop-20261017-context-manifest/`。旧七项GREEN只是阶段证据；最终冻结/全量/16核心及独立复审以本片报告为准。原26项仍PARTIAL，下一P03剩余片为Git安全导入，随后按P00–P19原依赖推进。


## P03 前置增量：所选 Git 版本只读预览

临时视图创建前，临时父目录若等于或位于源/common根内即拒绝，零目录创建；创建后再核对唯一owned子目录严格位于临时根内且不与源/common双向包含。临时父是源祖先时可使用独立兄弟目录；成功、失败、取消只清理本次owned子树。INV-01按既有契约显式登记O_RDONLY保守扫描及临时HEAD/config/index副本用途，保留原扫描与断言。

POST `/api/v1/projects/{project_id}/source-grants/{grant_id}/git-source-previews` 复用当前 local/owner/member 与既有同项目 SourceGrant；viewer 拒绝。body 恰 schema_version=1、expected_revision、paths、metadata_grant_id/null、metadata_grant_revision/null；不能自报根、Git argv、摘要或正文。仅显式1–200路径；不同原字节版本累计10MiB、公开摘要512KiB、索引4MiB、元数据边界5000文件/64MiB/深度32。Git子命令固定argv、单进程20秒读取时限，整次25秒预算包含owner锁等待和最终权限/UTC；同步OS磁盘I/O不能硬中断，返回前monotonic拒超预算成功，子进程取消后kill并有界排空回收2秒，未确认回收报错且不复用。未创建SessionLog资源、blob、队列或Provider。

ordinary `.git`必须为源根内无链接普通目录；linked `.git`仅是根内有界指针，外置common目录必须由owner另签同项目只读SourceGrant并显式选定metadata grant版本。common/worktrees直接子目录、commondir和gitdir回指均严格绑定；无外置授权先拒绝，不读取外部HEAD/index/objects。授权在所有磁盘/进程读取后再次核对角色、归档、根身份、版本及到期。所选全部名称先执行秘密内容策略再允许任何正文或Git读取。

读取临时bare视图仅写本程序临时HEAD/配置/索引副本，授权objects只读；不消费源config、hooks、filters、fsmonitor、diff/textconv或用户全局配置，不联网/取缺失对象；拒alternates/promisor/稀疏/未合并/submodule/LFS和链接/硬链等未支持情形。源HEAD/index/布局首尾一致，工作文件完整身份与哈希首尾一致，否则409不自动重采。stable_observed只是本次稳定观察，非OS原子快照或执行隔离。

严格GitSourcePreview只返摘要：每项HEAD/index/working GitOID、SHA256、原字节大小、统一Git模式100644/100755；工作层另返native_mode物理权限，Git层native_mode=null；无工作层为删除标记、无索引但有工作层为显式未追踪，另有暂存删除标记。BOM/CRLF参与原字节摘要，绝不返正文或绝对根。完整规范响应除observation_digest自身外计算SHA256；Web严格核对字段、绑定、清单、大小、模式及WebCrypto摘要后才展示。编辑、元数据选择、刷新、ESC及父授权观察统一失效，迟到结果不能复活确认/加载状态；未确认不发读取请求，无自动重试。

本增量仅受权只读预览，不是持久WorkspaceSnapshot、完整dirty导入、ContextManifest Git来源或执行准入。下一小片仍为P03持久Git WorkspaceSnapshot及来源绑定，后续P04/P05等按原依赖；全部26需求保持PARTIAL。验收以desktop-20261017-git-preview新冻结/full JUnit/实际exit/16核心逐项/Chrome合成API与真实HTTP独立标记/独立审查为准。

## P03 增量：持久 Git WorkspaceSnapshot / ContextManifest schema2

上述 Git 预览契约保持不变。本增量新增 `workspace_snapshot`（ws-16hex，revision=1，不可变），复用唯一 ApplicationService / WorkspaceStore owner / SessionLog / 不可变 blob，事件词表82、强同步16不变，不建立第二事实源、任务队列或执行器。私有原字节以SHA256寻址，清单blob先写入并精确读回，再强同步追加控制事件，最后核对磁盘收据；所有定义和当前投影可由日志重放重建。

输入 `paths` 须显式列出完整HEAD/index tracked并集及所需untracked，1–200项；遗漏tracked、秘密材料、非UTF8、链接、硬链接、submodule/LFS、稀疏/冲突索引或超额均整次拒绝，不自动补选/截断。未选untracked不进入私有内容。普通仓库及外置common另有精确同项目授权的linked worktree沿既有固定Git argv/安全bare视图读取；不消费源config/hooks/filter/fsmonitor或网络。不同版本原字节累计10MiB，保留BOM/CRLF。scope=tracked_plus_selected_untracked，exclusions=[]，不宣称全部未跟踪文件或任意二进制仓库均被导入。

`staged`比较HEAD/index GitOID及Git模式；`unstaged`比较index/working的存在性及原字节SHA256。工作层物理权限与Git层模式差异另以working_mode_differs表示，change_basis=raw_layer_comparison；不消费源core.filemode/autocrlf/filter，不宣称与任意仓库git status显示完全等价。工作层删除、暂存删除、显式未追踪分别标记。stable_observed非OS原子快照；源/HEAD/index/布局或完整文件身份变化拒绝409，不自动重采。

API：

- POST `/api/v1/projects/{project_id}/source-grants/{grant_id}/workspace-snapshot-previews`：schema_version=1、project_revision、expected_revision、paths、metadata_grant_id/revision；返回有界版本观察及confirmation_digest，零持久提交。
- POST 同前缀 `/workspace-snapshots`：加snapshot_id、confirmation_digest及Idempotency-Key；捕获重新读取并比对确认，不以历史预览字节作为当前授权。
- GET `/api/v1/projects/{project_id}/workspace-snapshots`：有界摘要分页；GET `/{snapshot_id}`精确版本；GET `/{snapshot_id}/items/{input_index}/layers/{head|index|working}`只读确切私有原字节，Content-Digest及Cache-Control:no-store。
- GET/POST `/api/v1/projects/{project_id}/workspace-context-manifests`：严格schema_version=2。POST含manifest_id、expected_revision、project_revision、source_ref（snapshot_id/revision=1/digest/source_seq）、可选既有TaskContextReference版本引用；其余来源字段和items仅由服务端派生，不接受前端自报根/正文。
- GET 同清单前缀 `/{manifest_id}/revisions/{revision}`精确读回；GET同版本 `/items/{original_input_index}`仅当前清单允许，删除留下的索引空位不重新编号。

Snapshot的完整来源确认绑定项目版本、主/元数据授权版本和source_seq、根身份、Git HEAD/index、所选层字节及工作文件身份承诺。首次私有写入前和所有私有写入后的提交决定前，均再次核对源稳定性、授权、当前主体/角色与最终UTC；私有内容在最后manifest写入后再完整核对，不能提交指向损坏先前blob的引用。私有存储与主源/外置common两个方向均须不重叠；捕获及CMv2首写/最终gate都执行同一几何检查。预算25秒含锁等待，不取消或释放其他持锁者；同步OS I/O不能硬中断，提交开始后结果不明按unknown处理。

每项目最多200次WorkspaceSnapshot历史提交；清单沿既有每类型200次历史提交及512KiB限额。预览、私有定义及完整公开快照收据均有字节预算，收据预算在首次私有写入前检查，预留后续stale原因空间。列表不返回items/tracked_paths/selected_untracked_paths：默认25项、limit=1–50、单响应512KiB，按source_seq稳定升序；响应含schema_version、snapshots或manifests、total、page_count、has_more、next_cursor、source_watermark、view_digest。游标限定项目/当前主体/类型/limit/水位下的已授权摘要视图；它是位置和视图一致性检查，不是授权凭证或密码学签名。每页重新验证当前权限/根/UTC；视图变化返回409要求刷新，游标格式/limit/重复参数/未知参数拒绝422。重启保持同视图顺序与水位，新提交须刷新才进入旧水位视图；不会截断隐藏合法的同水位数据。

WorkspaceSnapshot和CMv2均为创建主体私有，跨主体/项目/租户拒绝；来源撤权、到期、根删除/身份失效或项目归档拒绝历史元数据、文件及幂等收据。snapshot只保存已捕获历史字节，live_source_freshness=not_asserted；不证明活文件最新。项目版本变化或同主体/同授权的新snapshot令旧版本派生stale。CMv2绑定项目、可选任务的确切版本及完整WorkspaceSnapshot来源；项目/任务/清单升版或来源superseded时stale/items=[]，当前文件读取409；任务退役拒读。显式刷新可将仍为最新且受权的历史捕获绑定当前项目/任务版本，captured_project_revision始终保留原版本，不伪装为重新捕获。

schema1 ContextManifest及既有SourceSnapshot接口保持严格隔离；两类清单共享cm身份/控制协议但不能跨schema升降版、改创建主体、父项目、task身份或主源授权。TaskContextReference仍仅context_reference_only/executable=false，不产生ProductTask、模型调用、执行准入、宿主写入或副作用授权。Task、WorkspaceSnapshot、CMv2若真实提交后授权消失，拒绝返回收据并以503标记结果待核对，不能误报明确未提交的403而丢原key/body；恢复只核对原键及原正文，不自动重放未知副作用。

Web由独立workspace-input-contracts/workspace-inputs模块接入源授权行。严格字段/来源/派生摘要及WebCrypto收据检查后，再精确GET一致才显示持久确认；摘要hash仅证一致性，服务端授权与控制事实仍是权威。可读取有界分页、私有快照、任务引用和CMv2，显式刷新同一任务/清单版本；标题编辑保留原CM身份。观察epoch与命令生命周期分开：编辑/刷新清除旧预览、确认与选择，但同一对话中的原命令完成状态仍可见；关闭重开/跨行切换隔离旧响应。未知保留完整原命令，只有显式只读核对或原样重试，无自动重发。正文不自动载入界面，所有展示通过textContent/Option。

本片证据：`.ai-coding/evidence/desktop-20261017-workspace-snapshot/`，当前/最终冻结、真实exit/JUnit、16核心逐项门、Chrome合成API和真实HTTP链分别标记。当前仍不是完整P03执行隔离、P04交付中心或P05任务准入；全部26需求及P00–P19依赖保留PARTIAL。


## desktop-20261017 P03 显式 Git 候选元数据与权限预演

POST `/api/v1/projects/{project_id}/source-grants/{grant_id}/git-input-candidates` 是独立的只读业务预演（没有幂等提交或内容捕获）。严格 schema1 命令绑定当前项目 revision、主 SourceGrant revision，可选 linked 精确 metadata Grant revision；include_ignored 必须布尔，limit 默认25/允许1–50，cursor 有界。主体/租户从可信账户通道派生；成员可观察，viewer/撤权/跨团队/跨项目或旧项目版本均拒绝。每次请求复用 workspace owner 与25秒含锁等待预算；响应构建完成后再核对 source identity、当前角色、根metadata、可信UTC和预算。没有新的SessionLog事件、永久blob、执行队列或Provider调用。

公开候选页绑定 project/grant/source_seq、HEAD OID、index digest、metadata授权及当前view_digest。逐项仅给 HEAD/index OID与模式、working大小/mtime/native mode、tracked/untracked/ignored来源与删除标记；staged是HEAD/index元数据差异，工作区内容及unstaged尚未检查。秘密名或不支持项只返回名称摘要与排除理由，不回显路径；required tracked被排除或超过快照200项时不能假装完整可导入。最多2000候选、2MiB名称输出、单页512KiB，超限整次拒绝。cursor绑定主体、租户、项目/授权/参数与完整来源metadata视图，每页重新核权；不是权限token/HMAC。变化返回409要求刷新，无静默截断。page_digest逐项严格核验，只证明响应一致而非授权。

普通候选正文不读取、不保存；为分类显式读取有界工作树 `.gitignore` 规则元数据文本（body_policy=git_metadata_and_ignore_rules_only，ignore_policy=worktree_gitignore_only），不声称所有候选名对应字节均绝无读取：规则文件本身也可能是候选。受控分类不采用源info/exclude或全局忽略配置。规则presence/FD identity与目录/名称metadata均首尾锚定，自忽略规则变化也失效。元数据遍历上限10000条/2MiB名称，规则文本累计512KiB；不支持链接/junction、非普通或hardlink规则、Windows非标准大小写规则名及嵌套仓库则整体拒绝，拒绝前不读取其正文。规则初始path identity后打开O_RDONLY descriptor，首次read之前验证FD identity、regular及nlink=1，读后验证长度/FD/path。原生Git只在既有owner检查、与源/common双向隔离的随机TemporaryDirectory中枚举临时元数据树：普通文件仅空占位，规则仅经上述验证的有界拷贝。原生进程不读取宿主忽略规则；外部hardlink替换既不能影响原生分类，也不能在规则FD校验前读字节。临时树不是WorkspaceSnapshot，源普通正文不会进入该树；原生Git退出后清理，最终gate再核源。稳定观察不保证OS原子快照或完整DLP。

Web `显式列出 Git 候选元数据` 按钮先清除父面板旧预览和read/save确认，打开候选窗口不查询；用户再点击读取才请求API，所有项默认不选。分页仅累积同一view的候选，明确选择全部可用tracked及需要的untracked/ignored；1–200项、排除required为0才允许应用。应用前重新读取同一来源视图，变化/权限丢失/摘要错误即清选择；迟到查询/应用、编辑、刷新、ESC/跨行关闭不能恢复旧选择或loading。应用只回填名称草稿并再次清全部确认，不生成内容捕获许可。之后现有安全正文预览从服务端重新读取实际Git层、检查有限秘密策略，用户明确确认后才走现有WorkspaceSnapshot首次私有写入前和最终提交授权/稳定性gate。候选view不会被冒充为WorkspaceSnapshot来源ref，最终CMv2来源只能绑定真实已提交不可变WS的id/revision/digest/source_seq及服务端派生完整来源字段。

刷新/重启不从浏览器存储恢复旧成功或确认；候选须由当前真实API重新观察并核权，持久快照/清单则按既有服务端列表与确切历史GET恢复。候选没有第二权威索引，旧cursor在同主体且来源未变时可重开继续，来源/角色变化则拒绝。既有WS/CM原键未知恢复、日志重放与projection rebuild契约保持不变。本片仅P03阶段一/二和必要UI接缝；执行准入、宿主回写、P04交付中心/P05收件箱及其余26需求仍按原依赖验收。

共享 Git 元数据 reader（.git指针、commondir/backlink、HEAD/refs/packed-refs/index）同样在第一次字节读取前检查打开FD的identity/regular/nlink=1；identity含链接数。外部inode替换或同inode新增硬链接均先按既有EVT-101/git_metadata_changed拒绝，读后长度/FD/path复核保留。规则mirror不能替代其上游Git元数据首次读取检查。


## desktop-20261017 P03 不可变自有输入与受限隔离交接

`workspace_input`（wi-16hex，schema1、revision1不可变）沿唯一WorkspaceStore owner/SessionLog/blob协议持久化。POST `/api/v1/projects/{project_id}/workspace-inputs`严格仅接受input_id、project_revision及manifest_ref(id/revision/digest/source_seq)，加原Idempotency-Key；actor、来源17字段、task_ref、工作层文件与删除标记由服务端从确切current CMv2/WS派生。CMv1不是Git来源，不伪造Git引用，本片明确拒绝。GET同前缀有界列表及`/{input_id}`精确读回；成员仅本人私有事实，跨项目/租户/主体拒绝。旧版本项目/清单/任务/WS派生stale，文件与删除数组清空；撤权、到期、根身份失效、归档、任务退役拒读。历史重放按事件时绑定验证，未来到期不能损坏已提交历史。

准备在精确自有临时目录只复制私有已捕获working原字节，保留BOM/CRLF、路径与native_mode记录；删除不从HEAD/index补回。不读取当前宿主正文，不采用宿主配置。精确文件/目录集合、digest、size及模式核验通过后才提交不可变定义；正常/失败/取消清理本请求目录。单次200文件及删除标记、working总量10MiB、定义/完整收据512KiB、每项目该类型200次历史，25秒含锁等待。限额整次拒绝，无静默截断。复制采用exclusive FD，不在close后对路径chmod，拒绝链接/junction/hardlink、替换、额外文件、损坏、源/common/私有存储双向重叠。POSIX副本期望0600，Windows仅create模式及继承ACL尽力约束；没有证明Windows ACL等价POSIX，不能称OS沙箱。

收据明确workspace_state=not_started、executable=false、materialization_status=checked_at_commit、materialization_lifetime=per_explicit_use。持久化的是核验后的输入定义，不是持续存活的工作目录。每次显式使用必须重新核权、物化和验证。服务端可信`workspace_input_transfer`仅在owner锁内提供一次性Python capability；SandboxManager只接受isolated+empty+source=None，分配前检目标几何，后端create前后核完整集合及权限，退出后capability失效。后端不可用不fallback，准备API不启动后端/执行器/Provider/队列。合成后端可验证交接，不能替代真实Docker、原生Qt/WebView2或执行准入验收。

所有source普通FD读取及Git blob启动前重查角色、项目、主/metadata授权、根/UTC与预算；公开旧Git预览入口亦使用同一读取gate。阻塞授权I/O后再核FD/path完整身份（含nlink），首次read前拒绝替换。私有blob/definition读回采用同一checked-FD机制，无法读回则PERS-202，无宿主fallback。准备先构建收据再最终授权门；实际提交后撤权/读回失败为503 unknown，旧键只读收据当前无权则拒绝。空列表亦无条件尾预算检查。

Web先手动核对确切CM/WS，再独立显式确认；默认打开零查询零确认。严格字段、来源、模式、摘要及POST/GET全体一致才显示持久确认。观察epoch与原命令生命周期分开，编辑/刷新/跨行/ESC及迟到响应不复活确认或loading。首次unknown之后后续403/409仍保留原key/body，直到精确只读核对或用户显式原样重试成功；明确首次precommit拒绝可释放命令，无自动重放。刷新重开只读持久定义，不自动执行准备。receipt_digest仅一致性hash，不是授权或独立身份认证。

当前证据`.ai-coding/evidence/desktop-20261017-owned-inputs/`逐列保留RED、target、真实Chrome合成API、真实loopback HTTP/SessionLog/磁盘与冻结最终全量/16核心/独立审查。后者未完成前不得称本片通过；本片不等于完整P03、P04/P05或全部26，原22+实验室4需求及依赖保持PARTIAL。


## desktop-20261017 P03 显式选中文件/CMv1 自有输入接缝

原schema1命令/定义仍仅Git CMv2；新增PrepareSelectedInput/SelectedWorkspaceInput schema2明确选CMv1及已提交SourceSnapshot，不伪造Git字段。source_ref严格含source_kind=source_snapshot、ss id/revision/digest/source_seq、grant id/revision/source_seq、root_identity、confirmation_digest。policy=private-selected-copy-v1；文件元数据与捕获native_mode从确切私有事实派生，applied_mode=0600，deletions恒[]。CM按原主体私有语义，来源按既有团队SourceGrant读取边界；多个显式子集共存，不套用Git整树latest失效规则。

新增GET /api/v1/projects/{pid}/context-manifests/{mid}/revisions/{rev}/input-descriptor：只读当前CMv1、有界来源/文件元数据与descriptor_digest，不返回正文、input_id或假提交序号；persisted=false/executable=false。摘要是观察一致性，不是授权token。重放仍检查私有blob完整性；本入口不读宿主正文。准备共享已有workspace-inputs API、唯一owner/SessionLog/blob、200文件/10MiB/512KiB/200历史/25秒预算、checked-FD/当前角色根UTC/临时清理/原键未知恢复/稳定分页与一次性transfer。新旧定义共享配额；控制事件信封仍schema1，不新增事件名。

CMv1 Web入口先读取确切持久清单，再打开所选输入面板（打开零查询/确认）；手动读取确切CM及descriptor、独立确认、POSTschema2及严格GET一致后显示持久确认。编辑/刷新/跨行/ESC不复活旧观察、loading或确认；未知原key/body保留，无自动恢复副作用。记录是not_started/per_explicit_use，准备不授权执行或回写；Windows继承ACL仍非POSIX权限或OS隔离证明。P04文档引用前置接缝增量；原26/P00–P19仍PARTIAL，最终当前冻结测试与独审证据见desktop-20261017-selected-inputs，不套用上一Git片验收。


独审新增P2回归：未知输入命令关闭后遇清单升版，父页仅有新revision时仍保留原绑定元数据；子页提供显式“返回原绑定核对未知命令”，只切原绑定/旧清单，零查询零重发零确认。之后用户显式只读GET原wi及确切stale收据，或显式原key/body重试；不能把新revision当前上下文混入原请求。两个来源和真实父清单刷新流程均有Chrome回归。旧449全量因发现该缺陷受控中断，实际退出及151浏览器结果保留superseded-449，不计本最终验收。
