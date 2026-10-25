# GitHub Token 获取指南

## 为什么需要 Token？

GitHub API 有访问限额：

| 身份 | 限额 | 说明 |
|------|------|------|
| 匿名 | 60 次/小时 | 需要频繁采集时不够 |
| Token | 5,000 次/小时 | 推荐使用 |

## 获取步骤

### 1. 登录 GitHub
访问 [GitHub Settings](https://github.com/settings)

### 2. 进入令牌管理
- 左侧菜单：**Developer settings** → **Personal access tokens** → **Tokens (classic)**

### 3. 创建令牌
点击 **Generate new token** → **Generate new token (classic)**

### 4. 设置权限
勾选必需权限：
- ✅ `public_repo` - 读取公共仓库信息
- ✅ `read:org` - 组织仓库访问

> **安全提示**：
> - 创建后**仅显示一次**，请及时保存
> - 可在「Settings → Developer settings → Personal access tokens」查看历史令牌

### 5. 使用令牌

在 `.env` 文件中配置：

```bash
GH_GITHUB_TOKEN=ghp_你的令牌代码
```

## 令牌安全

### 前端如何隐藏
- 后端返回时脱敏：`sk-t******3456` 显示
- 实际调用 API 时使用完整 token

### 环境变量加密
Docker Compose 会自动注入到容器中：

```yaml
environment:
  GH_GITHUB_TOKEN: ${GH_GITHUB_TOKEN:-}
```

## 常见问题

**Q：令牌设置了哪些权限？**
A：最小权限即可，`public_repo` 已足够。

**Q：如何监控限额？**
A：访问 `http://localhost:9951/api/system/rate-limit` 查看剩余次数。

**Q：令牌过期怎么办？**
A：重新生成 token，更新 `.env` 后执行 `docker compose restart api`。