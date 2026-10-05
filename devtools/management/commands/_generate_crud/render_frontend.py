#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：前端三件（api.ts / hook.ts / index.vue）模板渲染。

RenderMixin 按域拆分（文件行数门禁）的前端部分；组合与入口见 renderers.py。
"""


class RenderFrontendMixin:
    """前端模板渲染。"""

    def _render_client_api(self, ctx):
        lines = [
            'import { BaseApi } from "@/api/base";',
            "",
            f"/** {ctx['verbose_name']}（generate_crud 生成）",
            " *",
            " * 自定义 action 用子类方法追加，参考 src/api/system/task.ts。",
            " */",
            f'export const {ctx["model_snake"]}Api = new BaseApi("/{ctx["url_prefix"]}");',
            "",
        ]
        return "\n".join(lines)

    def _render_client_hook(self, ctx):
        lines = [
            'import { reactive } from "vue";',
            'import { usePageAuth } from "@/router/utils";',
            "",
            f'import {{ {ctx["model_snake"]}Api }} from "./api";',
            "",
            f"/** {ctx['verbose_name']} 页面逻辑（generate_crud 生成）",
            " *",
            " * 表格列/搜索/表单覆写按需追加：listColumnsFormat / searchColumnsFormat /",
            " * addOrEditOptions，参考 xadmin-docs example/new-app-client.md。",
            " * 自定义按钮：工具栏 tableBarButtonsProps / 行内 operationButtonsProps，",
            " * code 需对应权限码 <action>:" + ctx["component"] + "（菜单管理挂权限点），",
            ' * 自定义权限位以 extraKeys 形式传入 usePageAuth，如 usePageAuth(["customAction"])。',
            " */",
            f"export function use{ctx['component']}() {{",
            f"  const api = reactive({ctx['model_snake']}Api);",
            "  const auth = usePageAuth();",
            "",
            "  // 自定义按钮样板（取消注释后按需修改），完整范式参考 src/views/system/dict/utils/hook.tsx：",
            "  // const tableBarButtonsProps = {",
            "  //   buttons: [",
            "  //     {",
            '  //       text: "自定义按钮",',
            '  //       code: "customAction",',
            '  //       props: { type: "primary", plain: true },',
            "  //       onClick: ({ loading }) => {",
            "  //         loading.value = true;",
            "  //         api.customAction().finally(() => (loading.value = false));",
            "  //       },",
            "  //       show: auth.customAction && 1",
            "  //     }",
            "  //   ]",
            "  // };",
            "  // const operationButtonsProps = {",
            "  //   buttons: [",
            "  //     {",
            '  //       text: "行内动作",',
            '  //       code: "rowAction",',
            '  //       props: { type: "primary", link: true },',
            "  //       onClick: ({ row }) => api.rowAction(row.pk),",
            "  //       show: auth.rowAction && 1",
            "  //     }",
            "  //   ]",
            "  // };",
            "",
            "  return { api, auth };",
            "}",
            "",
        ]
        return "\n".join(lines)

    def _render_client_page(self, ctx):
        lines = [
            '<script lang="ts" setup>',
            'import { RePlusPage } from "@/components/RePlusPage";',
            "",
            f'import {{ use{ctx["component"]} }} from "./utils/hook";',
            "",
            "defineOptions({",
            "  // 必须唯一：权限码按 动作:组件名 匹配",
            f'  name: "{ctx["component"]}"',
            "});",
            "",
            f"const {{ api, auth }} = use{ctx['component']}();",
            "</script>",
            "",
            "<template>",
            f'  <RePlusPage :api="api" :auth="auth" locale-name="{ctx["locale_name"]}" />',
            "</template>",
            "",
        ]
        return "\n".join(lines)
