"""邮件触达层（SPEC-J J4）：模板渲染 / 预算链 / 双通道发送 / 调度。

分层：templates(纯渲染) → budget(纯预算) → sender(通道IO) → dispatcher(编排)。
dispatcher 可注入 send_fn 与收件源，测试无需真实邮件服务。
"""
