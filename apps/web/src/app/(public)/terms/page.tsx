import type { Metadata } from "next";
import { PublicSiteHeader } from "@/components/layout";
import {
  LegalDocumentPage,
  type LegalDocumentSection,
} from "@/components/legal/LegalDocumentPage";
import { termsRoute } from "@/lib/routes";

export const metadata: Metadata = {
  title: "服务条款",
  description: "Claread 测试期服务条款草案。",
  robots: { index: false, follow: false },
};

const sections: readonly LegalDocumentSection[] = [
  {
    id: "service",
    title: "服务",
    content: (
      <>
        <p>
          Claread 是以文章为中心的阅读工具。测试期服务可能包括材料导入、阅读记录、原文与译文呈现、词汇和笔记整理，以及围绕当前文章的 Ask 功能。具体功能以页面实际提供的内容为准。
        </p>
        <p>
          你可以使用公开页面，也可以在具备相应访问条件时使用账号功能。服务会持续调整，某些功能可能只在测试环境或特定客户端开放。
        </p>
      </>
    ),
  },
  {
    id: "account",
    title: "账号",
    content: (
      <>
        <p>
          使用需要登录的功能时，你应提供真实、准确且仍可接收邮件的账号信息，并妥善保管登录凭据、验证码和会话。账号下发生的操作，通常会被视为由账号使用者发起；如果你发现未经授权的使用，应尽快停止使用并通过正式渠道报告。
        </p>
        <p>
          账号注册、邮箱验证、密码设置、密码重置和会话管理受当时可用的流程约束。账号资格、恢复方式及正式上线后的管理规则，仍属于待确认的产品事实。
        </p>
      </>
    ),
  },
  {
    id: "user-content",
    title: "用户内容许可",
    content: (
      <>
        <p>
          你保留对自己提交的文章、链接、文件、阅读记录、词汇、笔记和 Ask 内容所拥有的权利。你应确保自己有权提交这些内容，并且提交不会违反法律、合同、保密义务或第三方权利。
        </p>
        <p>
          为提供、维护、保护和改进测试期服务，你授予 Claread 一项非独占、全球范围、在服务存续及必要备份期间有效的许可，用于接收、存储、复制、格式转换、展示、分析、生成译文或其他理解信息，以及按服务需要交由相关技术服务商处理。除实现这些目的外，不把这段许可理解为 Claread 取得你的内容所有权。
        </p>
      </>
    ),
  },
  {
    id: "prohibited-use",
    title: "禁止行为",
    content: (
      <>
        <p>使用服务时不得：</p>
        <ul className="list-disc space-y-2 pl-5">
          <li>违反法律、侵犯他人知识产权、隐私或其他权利，或提交你无权处理的材料；</li>
          <li>上传恶意代码、试图破坏服务，或以会造成不合理负载的方式批量抓取、调用或滥用接口；</li>
          <li>绕过访问控制、验证码、限流、内容安全措施或其他技术限制；</li>
          <li>反向工程、复制服务的非公开实现，或冒充他人、误导他人了解内容来源；</li>
          <li>把模型、翻译或 Ask 输出用于违法、危险或未经核验的高影响决定。</li>
        </ul>
      </>
    ),
  },
  {
    id: "intellectual-property",
    title: "知识产权",
    content: (
      <p>
        Claread 的名称、标识、网站、软件、界面、文档和未由用户提供的内容，及其相关知识产权，归相应权利人所有。除本条款明确允许的服务使用外，不因访问或使用服务而转让任何所有权或其他权利。第三方材料、链接和模型输出可能受各自权利人的条款约束。
      </p>
    ),
  },
  {
    id: "ai-limitations",
    title: "AI 与翻译结果限制",
    content: (
      <>
        <p>
          译文、词汇解释、语法说明、结构分析和 Ask 回复由自动化系统辅助生成，可能不完整、不准确、过时或不适合你的上下文。相同输入也可能得到不同结果；结果不代表专业的法律、医疗、财务、教育或其他领域意见。
        </p>
        <p>
          在作出重要决定、发布内容或依赖某个事实前，你应回到原文和可靠来源自行核验。Claread 不承诺输出无错误，也不承诺模型会识别所有风险、版权限制或敏感信息。
        </p>
      </>
    ),
  },
  {
    id: "third-party-services",
    title: "第三方服务",
    content: (
      <p>
        服务可能依赖邮件发送、数据托管、数据库、缓存、文件或对象存储、模型和其他基础设施服务商。第三方服务有自己的可用性、隐私和使用规则，相关变化可能影响 Claread 的功能。测试期页面不把任何第三方服务的持续可用性、处理方式或安全结果承诺为 Claread 的保证。
      </p>
    ),
  },
  {
    id: "beta-availability",
    title: "Beta 可用性",
    content: (
      <p>
        Claread 当前处于测试期。功能、接口、内容和数据结构可能变更、暂停或移除，服务可能出现中断、延迟、错误或测试数据重置。请自行保留重要材料的副本，不要把测试期服务当作唯一存储、关键业务系统或有固定服务等级的产品。
      </p>
    ),
  },
  {
    id: "termination",
    title: "终止",
    content: (
      <>
        <p>
          你可以停止使用服务。为处理安全风险、违反本条款、违法请求或服务迁移，Claread 也可能限制、暂停或终止部分功能或账号访问。测试期的通知、申诉和数据处理细则尚未定稿。
        </p>
        <p>
          终止后，部分内容可能不再可访问；本草案不承诺固定的恢复、导出或保存期限。与用户内容权利、知识产权、免责声明、责任和争议相关的条款，按其性质在终止后继续适用。
        </p>
      </>
    ),
  },
  {
    id: "disclaimer",
    title: "免责声明",
    content: (
      <p>
        测试期服务按当时可提供的状态提供。页面信息、译文和自动化结果不保证持续、完整、及时、适合特定目的或没有中断；网络、设备、第三方服务和用户提供的材料也可能造成不可控的错误。这里不作“绝对安全”或“完全合规”的承诺。
      </p>
    ),
  },
  {
    id: "liability",
    title: "责任限制",
    content: (
      <p>
        由于测试期的性质，使用服务的风险和可能损失应由使用者结合材料重要程度自行评估。本条款不作无限责任豁免，也不试图排除依法不能排除的责任；责任范围、例外和适用规则在正式版前仍需由 Owner 确认并重新审阅。
      </p>
    ),
  },
  {
    id: "dispute-contact",
    title: "争议与联系",
    content: (
      <>
        <p>
          本测试期草案不指定运营主体、联系地址、适用法律或争议管辖，也不构成正式争议解决安排。正式上线前，Owner 必须补充运营主体及地址、support/privacy 邮箱、适用法律与争议解决方式。
        </p>
        <p>
          当前页面仅用于公开展示待确认的条款框架；正式联系渠道启用前，不要把本页视为已建立的通知或送达地址。
        </p>
      </>
    ),
  },
];

export default function TermsPage() {
  return (
    <LegalDocumentPage
      title="服务条款"
      summary="这是一份面向 Claread 测试期服务的中文条款框架，用来说明服务边界、用户内容和自动化结果的使用风险。"
      header={<PublicSiteHeader currentHref={termsRoute} showCta={false} />}
      sections={sections}
    />
  );
}
