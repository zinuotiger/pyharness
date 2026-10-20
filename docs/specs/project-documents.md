# S05 项目资料、版本化引用与交付定义

本步覆盖A04文档阅读、A05引用定位、A06过期/缺失提示及A02交付清单定义/修订。A02独立验收绑定仍在S10；不创建RunAttempt、不调用模型、不授权宿主回写。完成状态以任务外S05冻结证据为准，不能用代码存在代替验收。

## 类型与唯一事实

严格模型位于`application/document_contracts.py`，全部extra=forbid、strict、frozen；schema版本必须为整数1。DocumentRevision（doc-16hex）和DeliveryRevision（del-16hex）最多200修订，引用CitationRevision（cit-16hex）不可变且只能第1版；三种资源各按项目累计200次历史提交预算。所有权/租户由Projects服务端决定，既有SessionLog按`workspace.resource_committed`追加唯一事实，先确认不可变blob再强同步并核磁盘。既有schema及事件词表保留，不能把TaskContextReference当正式ProductTask。

DocumentRevision包含来源绑定、原始content_digest/size_bytes、解析器版本、format、物理页总数及不可定位页、Unicode段落块和能力状态。纯文本来源为确切已准备的WorkspaceInput ID/revision/digest/source_seq+file_index；服务器派生正文、路径、解析器、页/段，客户端不得自报。PDF来源单独固定SourceGrant ID/revision/digest/source_seq/root_identity、明确文件SnapshotFile与确认摘要，不虚构Git字段，不放宽旧文本快照策略。

DocumentBlock固定block_id/page/paragraph/start/end/text/quote_digest。文本无物理页，page=null，page_count=0；PDF page为1起的真实物理页，空白页保留其编号，不能用检索行号或去空白后的页序代替。offset单位是**解码后每一物理页文本/纯文本中的Unicode码点**，区间[start,end)，不是UTF8字节或JS UTF16单元。UTF8 BOM不进入解码文本；原始BOM/CRLF字节摘要不变，段内CRLF保留，空白行分段。

引用固定DocumentReference及block_id/start/end/quote_digest，必须精确匹配同版本、同块和相应片段SHA256。读取按历史修订定位，不追随最新；新建引用须资料当前且可定位。引用历史可显示stale的旧片段，但资料退役或源文件缺失时quote=null；撤权/到期/角色或租户变化拒读。

DeliveryRevision固定正式TaskVersionReference和交付items（唯一item_id、名称、必交标志、1–20条验收标准和0–20个确切引用）。每清单1–30项，CAS修订可绑定同一正式任务的新版本，不能换成另一任务身份；清单退役不可复活。返回`acceptance_status=not_evaluated`、`execution_authorized=false`、`export_authorized=false`，没有下载/验收/执行旁路。

## 来源与能力

| 格式 | 阅读能力 | 定位与边界 |
|---|---|---|
| UTF8/BOM文本、Markdown、源码、HTML文本 | 从明确准备的私有输入解码，只显示文字 | 段落和Unicode区间；不执行HTML/脚本/宏，不虚构页码 |
| PDF文本层 | 独立授权预览并确认导入，固定pypdf解析器版本 | 真物理页和提取段落；普通URI/内部链接被忽略 |
| 无文本层/空白PDF页 | 明确标记不可定位页 | 不做OCR；有文本的其他页仍按原物理页定位 |
| 加密PDF、损坏PDF | 明确能力不可用及原因 | 不虚构正文/页数；当前无法解密/解析时页数记0 |
| JS/自动动作、启动/提交/外部文件动作、嵌入文件PDF | 拒绝导入 | 不保存永久资料引用；不执行文档动作 |
| Office/其他二进制、原PDF像素版式、OCR | 本步不支持 | 不以文本提取宣称排版保真；对应能力明确未提供 |

PDF上限：原文件2MiB、200物理页、单页内容流2MiB、累计解码内容流8MiB；文字96KiB、400块、控制blob512KiB；超限拒绝，不截断假成功。一次性解析器时间10秒、进程内存512MiB，独立进程组/Windows隐藏Job载体，超时/取消回收且不复用。只运行可信固定脚本，没有用户命令/模型/网络；主服务与唯一事实仍同一owner。架构窄例外见ADR-024。

PDF预览只查来源文件元数据和授权，不读正文、不落事实。确认摘要绑定主体/项目/授权/根/文件身份；导入重新校验元数据，再以现有checked FD读取明确文件，解析后尾核、确认私有原字节和定义，再追加日志。原文本快照仍拒绝二进制PDF。私有解析临时目录回收，源目录不得与私有控制存储重叠。确认摘要不是授权令牌。

读取只用私有捕获字节，不回读活源正文。非Git/PDF来源仅查活文件元数据：变化标source_file_changed，缺失标source_file_missing；未声称检测刻意恢复全部元数据的宿主改写。Git私有输入另在资料导入时记录文件元数据锚点，后续变化/删除也标过期/缺失；它仍不回读活源正文，live_source_freshness=metadata_only不等于证明活源字节未变。项目/清单/输入/资料修订、解析器版本变化均可标stale；源授权/根身份/账户不可用拒读。每个I/O尾重新核角色与UTC；列表末尾汇总所有依赖授权查时效，观察及等锁共25秒预算。

## API

全部前缀`/api/v1/projects/{project_id}`，使用已有workspace路由鉴权/异常映射和service lifetime。拥有成员资格的owner/member可管理自己的私有资料，viewer不能读取私有输入；同团队owner也不能接管他人资料。成员只增加下表精确POST形状，不扩管理权限。

| 方法/路径 | 作用 |
|---|---|
| GET/POST /documents | 有界列表；创建/CAS修订DocumentCommand，POST必填Idempotency-Key |
| GET /documents/{document_id}/revisions/{revision} | 精确修订、段落和当前权限/失效观察 |
| POST /document-pdf-previews | PdfPreview(project_revision,grant_id,grant_revision,path)，只读元数据预览 |
| GET/POST /citations | 有界元数据列表；创建精确CitationCommand，POST必填原键 |
| GET /citations/{citation_id} | 重新授权并定位引用原资料修订 |
| GET/POST /delivery-definitions | 有界清单列表；创建/CAS修订DeliveryCommand，POST必填原键 |
| GET /delivery-definitions/{delivery_id}/revisions/{revision} | 清单原修订，不等于验收结果 |

列表仅接受limit(1–50)、cursor；沿既有水位/主体/项目/视图绑定游标，错主体/视图/limit或变化拒绝并要求刷新，单响应≤512KiB。返回metadata的真实total，不把当前页长度冒充总数。无公开正文下载、任意路径读取或执行端点。

## Web

项目空间→资料与交付要求，分“资料阅读/保存的引用/交付要求”。文本显式选择输入并读取文件清单；PDF显式预览并勾选本次导入，编辑即废弃确认。阅读支持版本号、物理页/段和Unicode区间引用；定位在原修订高亮。清单使用普通项目表单，不要求用户编写JSON。

所有正文/title/标准安全转义或textContent，异步结果受dialog/epoch/主体约束，关闭/刷新/修改不能复活旧确认；撤权刷新清掉旧正文和列表。保存后按确切版本GET并核原命令才报持久确认；网络未知保留原key/body，可只读核对或显式原样重试，不自动新建。当前保留在浏览器内存，完整浏览器退出后需从服务端列表核对；S06负责正式任务提交的持久查询。

## 验证对应

| AC | 证据测试入口 |
|---|---|
| S05-01 | test_documents、test_document_boundaries的BOM/CRLF/Unicode/私有读取；documents.test.cjs的HTML和emoji |
| S05-02 | test_document_pdf的真实解析、空白物理页、扫描/加密/损坏/活动内容、原策略仍拒二进制；test_document_adversarial的真实超时载体回收 |
| S05-03/04 | document_boundaries/adversarial的精确修订、退役、源变化、角色/UTC、错区间/摘要、只读原文 |
| S05-05 | document_boundaries的正式任务+引用→交付定义、任务更新失效、拒绝自报accepted |
| S05-06 | documents/boundaries的重开、原键unknown、损坏保真、CAS、分页和主体隔离 |
| S05-07 | Chrome合成API交互与真实loopback HTTP→SessionLog→重开分列；冻结全量/覆盖/安装见本轮任务外证据 |

以上描述实现与验收入口；通过/失败计数必须读取对应当前证据，不能拿旧S04全量报告作S05证据。
