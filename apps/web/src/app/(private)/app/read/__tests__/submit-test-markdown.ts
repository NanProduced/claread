/**
 * 提交编辑器共享测试夹具：精确测试文本（任务书原文）。
 * 放在非 `.test.ts` 模块，避免被多个测试文件 import 时重复注册 describe。
 */

export const SUBMIT_TEST_MARKDOWN = `## 6. Implementation Plan

*How we will roll this out safely, step by step.*

Since this refactoring is extensive, and AAT has a large number of servers running different platform versions, we first need to confirm a stable version as the baseline before proceeding. The specific steps are as follows:

### Step 1: Streamline Server Deployment Architecture

Optimize the platform's deployment on servers by reducing the number of unnecessary containers, freeing up memory to be allocated to MongoDB. This step will not affect any platform functionality — all running services will be properly preserved and handled.

### Step 2: Data Storage Migration & Feature Adaptation

Migrate playback statistics and related data from MySQL to MongoDB, and implement the previously customized statistical features according to AAT's requirements.

### Step 3: Canary Deployment & Validation

After the changes are complete, we recommend performing a canary deployment on a test server first to validate the performance and behavior of the refactored service. Once confirmed stable, it can be gradually rolled out to all servers.`;
