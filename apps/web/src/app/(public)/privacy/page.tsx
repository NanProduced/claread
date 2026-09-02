import type { Metadata } from "next";
import { PublicSiteHeader } from "@/components/layout";
import {
  LegalDocumentPage,
  type LegalDocumentSection,
} from "@/components/legal/LegalDocumentPage";
import { privacyRoute } from "@/lib/routes";

export const metadata: Metadata = {
  title: "隐私政策",
  description: "Claread 测试期隐私政策草案。",
  robots: { index: false, follow: false },
};

const sections: readonly LegalDocumentSection[] = [
  {
    id: "scope",
    title: "适用范围与草案状态",
    content: (
      <p>
        本页按当前 Claread Web 与通用 API 代码中可观察到的数据流编写，用于测试期沟通，不是完整的数据盘点，也不是最终法律意见。随着运行配置、第三方服务和账号功能变化，本页需要同步更新。
      </p>
    ),
  },
  {
    id: "data-categories",
    title: "数据类别",
    content: (
      <>
        <p>服务可能处理以下类别的信息：</p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong className="font-semibold text-ink">账号与认证：</strong>邮箱、密码哈希、邮箱验证所需的一次性验证码（OTP）、挑战/票据状态，以及登录 session 标识。密码本身不应以明文保存。
          </li>
          <li>
            <strong className="font-semibold text-ink">阅读内容：</strong>你提交的阅读材料，包括粘贴内容、网页链接、文件、文件名或处理这些材料所需的元数据。
          </li>
          <li>
            <strong className="font-semibold text-ink">阅读资产：</strong>阅读记录、阅读进度、原文及其处理结果、词汇、笔记、收藏，以及你在当前文章中发起或保存的 Ask 内容和相关回复。
          </li>
          <li>
            <strong className="font-semibold text-ink">技术与安全信息：</strong>IP 地址、设备信息或客户端信息、平台/版本信息，以及为认证、限流、错误排查和服务运行所需的必要日志与事件记录。
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "use-of-data",
    title: "使用目的",
    content: (
      <p>
        上述信息用于创建和保护账号、发送验证邮件、验证 OTP、建立或撤销 session、处理阅读材料、生成译文和理解信息、保存阅读资产、响应 Ask、提供支持、限制滥用、排查故障、维护数据一致性，以及保障测试期服务的运行。不会因为本页列出某种用途，就推断出当前代码已经实现了全部相应用户控制。
      </p>
    ),
  },
  {
    id: "cookies",
    title: "必要 Cookie",
    content: (
      <p>
        Web 端通过同源 Next.js BFF 使用必要的 HttpOnly Cookie 保存登录 session，以及邮箱验证流程所需的挑战、票据等状态。它们用于维持登录、完成验证和防止流程被滥用；challenge、ticket 和 session token 不进入普通浏览器 JSON 或认证日志，浏览器脚本也不能直接读取这些 Cookie。Cookie 的路径、有效期、Secure 和 SameSite 行为由当前服务配置决定，具体清单和正式通知文案仍需在上线前补齐。
      </p>
    ),
  },
  {
    id: "service-providers",
    title: "第三方服务与共享",
    content: (
      <>
        <p>
          为实现当前功能，必要信息可能由以下类别的服务商处理：
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong className="font-semibold text-ink">Resend：</strong>用于发送邮箱验证和密码重置邮件；收件邮箱及邮件流程所需内容会被交给邮件服务。
          </li>
          <li>
            <strong className="font-semibold text-ink">HIBP 密码安全接口：</strong>密码安全检查使用 k-anonymity 方式处理哈希前缀，当前实现不把完整密码或完整密码哈希作为查询内容发送。
          </li>
          <li>
            <strong className="font-semibold text-ink">托管、数据库和模型服务商：</strong>PostgreSQL 是业务数据的主要来源，Redis 用于认证挑战、票据和限流等辅助状态；文件/对象存储和按运行配置启用的模型、向量或搜索服务可能处理阅读材料、Ask 内容及其必要派生数据。代码配置中存在 DashScope/百炼、DeepSeek、Zilliz 等接入项，实际启用项以环境配置为准。
          </li>
        </ul>
        <p>
          第三方服务的具体名称、角色、处理地点、子处理方、保存策略和是否用于服务商自身训练，不能仅由本页推定；完整处理方清单及相应合同关系是正式版前的待确认项。
        </p>
      </>
    ),
  },
  {
    id: "cross-border",
    title: "境外处理",
    content: (
      <p>
        由于邮件、模型、托管或其他基础设施服务商可能在境外提供服务，相关数据可能发生跨境传输、访问或处理。当前代码和开发配置没有为本页确认完整的部署国家、服务器地区或跨境机制；这些事实须由 Owner 在正式版前补充，而不是在测试期凭推测承诺。
      </p>
    ),
  },
  {
    id: "storage-security",
    title: "保存方式与安全",
    content: (
      <>
        <p>
          账户、阅读记录、词汇、笔记、Ask 线程和相关事件主要通过 PostgreSQL 等数据库结构保存；认证挑战和部分限流状态使用 Redis。上传材料或派生文件可能按环境使用本地或对象存储。不同数据的权限、备份、删除和加密配置需要以实际部署为准。
        </p>
        <p>
          当前代码可确认的保护方式包括：密码以 Argon2id 哈希保存，服务端以哈希形式保存 session token，浏览器认证 Cookie 设置为 HttpOnly，并通过 HIBP 的 k-anonymity 查询降低密码检查暴露面。上述措施不等于“绝对安全”；漏洞、错误配置、第三方故障和传输风险仍可能发生。
        </p>
      </>
    ),
  },
  {
    id: "retention",
    title: "保存期限与删除",
    content: (
      <p>
        测试期暂不在页面承诺固定保存年限。OTP、挑战、票据和 session 有服务端过期或撤销状态，但这些流程期限不等于账号、阅读材料或用户资产的完整保存期限。正式版前，Owner 必须确认各类数据的保存期限、备份清理、删除传播范围，以及账号删除/注销的具体方式。
      </p>
    ),
  },
  {
    id: "user-rights",
    title: "访问、更正、删除和注销",
    content: (
      <p>
        你可以就个人信息提出访问、更正、删除或账号注销请求，也可以询问相关处理情况。当前测试期的身份核验、请求入口、处理时限、例外范围和删除后的残留数据规则尚未完全确定；在正式版前，Owner 必须补充可用的账号删除方式及 support/privacy 邮箱。
      </p>
    ),
  },
  {
    id: "minors",
    title: "未成年人",
    content: (
      <p>
        目标年龄、是否面向未成年人提供服务、监护人同意要求和未成年人数据处理策略，当前均未由 Owner 最终确认。测试期不通过本页作出年龄适配、监护同意或其他未成年人合规承诺；正式上线前必须明确这些规则和对应的产品流程。
      </p>
    ),
  },
  {
    id: "policy-updates",
    title: "政策更新",
    content: (
      <p>
        当数据处理、第三方服务或产品功能发生变化时，本页可能更新。页面会通过版本号和更新日期标记当前文本；正式版前还需确定重大变化的通知方式、生效规则、历史版本保存方式和负责发布的主体。
      </p>
    ),
  },
  {
    id: "contact",
    title: "联系与正式版待确认事项",
    content: (
      <>
        <p>
          本页不虚构运营主体、地址或邮箱。以下均属于 OWNER_DECISION_REQUIRED，正式版前必须补充并核验：
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>生产 HTTPS、同源 BFF 与可信反向代理/IP 处理边界；</li>
          <li>Resend 发信域名的 DKIM/SPF/DMARC 线上事实；</li>
          <li>HIBP 不可用时是否采用 fail-open 政策；</li>
          <li>运营主体及地址；</li>
          <li>support/privacy 邮箱；</li>
          <li>目标年龄与未成年人策略；</li>
          <li>服务器地区；</li>
          <li>完整处理方清单；</li>
          <li>各类数据的保存期限；</li>
          <li>账号删除方式；</li>
          <li>收费退款、适用法律与争议解决安排。</li>
        </ul>
      </>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <LegalDocumentPage
      title="隐私政策"
      summary="这是一份基于当前代码事实整理的测试期隐私政策草案，重点说明 Claread 会接触哪些数据、为何需要它们，以及哪些正式信息仍待确认。"
      header={<PublicSiteHeader currentHref={privacyRoute} showCta={false} />}
      sections={sections}
    />
  );
}
