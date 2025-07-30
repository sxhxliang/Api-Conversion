// AI API统一转换代理系统前端交互逻辑
class APIConverter {
    constructor() {
        this.currentTaskId = null;
        this.progressInterval = null;
        this.channels = [];
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupTabNavigation();
        this.loadProviders();
        this.loadCapabilities();
        this.loadChannels();
        this.setupModernAnimations();
    }

    setupEventListeners() {
        // 渠道表单提交事件
        if (document.getElementById('channelForm')) {
            document.getElementById('channelForm').addEventListener('submit', (e) => {
                e.preventDefault();
                const form = e.target;
                
                if (form.dataset.isEditing === 'true') {
                    // 编辑模式
                    const channelId = form.dataset.editingChannelId;
                    this.updateChannel(channelId);
                } else {
                    // 创建模式
                    this.createChannel();
                }
            });
        }

        // 检测表单提交事件
        if (document.getElementById('detectionForm')) {
            document.getElementById('detectionForm').addEventListener('submit', (e) => {
                e.preventDefault();
                this.startDetection();
            });
        }

        // 提供商选择事件
        if (document.getElementById('provider')) {
            document.getElementById('provider').addEventListener('change', (e) => {
                this.updateDefaultUrl(e.target.value);
                this.updateModelOptions(e.target.value);
            });
        }

        // 渠道提供商选择事件
        if (document.getElementById('channel_provider')) {
            document.getElementById('channel_provider').addEventListener('change', (e) => {
                this.updateChannelDefaultUrl(e.target.value);
            });
        }
    }

    updateDefaultUrl(provider) {
        const defaultUrls = {
            'openai': 'https://api.openai.com/v1',
            'anthropic': 'https://api.anthropic.com',
            'gemini': 'https://generativelanguage.googleapis.com/v1beta'
        };

        const baseUrlInput = document.getElementById('detection_base_url');
        if (defaultUrls[provider] && baseUrlInput) {
            baseUrlInput.value = defaultUrls[provider];
        }
    }

    updateChannelDefaultUrl(provider) {
        const defaultUrls = {
            'openai': 'https://api.openai.com/v1',
            'anthropic': 'https://api.anthropic.com',
            'gemini': 'https://generativelanguage.googleapis.com/v1beta'
        };

        const baseUrlInput = document.getElementById('base_url');
        if (defaultUrls[provider] && baseUrlInput) {
            baseUrlInput.value = defaultUrls[provider];
        }
    }

    updateModelOptions(provider) {
        const modelSelect = document.getElementById('target_model_select');
        if (!modelSelect) return;

        const commonModels = {
            'openai': [
                'gpt-4o',
                'gpt-4o-mini',
                'gpt-4-turbo',
                'gpt-4',
                'gpt-3.5-turbo'
            ],
            'anthropic': [
                'claude-3-5-sonnet-20241022',
                'claude-3-5-haiku-20241022',
                'claude-3-opus-20240229',
                'claude-3-sonnet-20240229',
                'claude-3-haiku-20240307'
            ],
            'gemini': [
                'gemini-1.5-pro',
                'gemini-1.5-flash',
                'gemini-1.0-pro'
            ]
        };

        // 清空现有选项
        modelSelect.innerHTML = '<option value="">请选择模型</option>';

        // 添加对应的模型选项
        if (commonModels[provider]) {
            commonModels[provider].forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                modelSelect.appendChild(option);
            });
        }
    }

    setupTabNavigation() {
        // 标签页切换事件
        const tabButtons = document.querySelectorAll('.tab-button');
        tabButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const targetTab = e.target.getAttribute('data-tab');
                this.switchTab(targetTab);
            });
        });
    }

    switchTab(tabName) {
        // 移除所有活动状态
        document.querySelectorAll('.tab-button').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });

        // 激活选中的标签页
        const activeButton = document.querySelector(`[data-tab="${tabName}"]`);
        const activeContent = document.getElementById(`${tabName}-tab`);

        if (activeButton && activeContent) {
            activeButton.classList.add('active');
            activeContent.classList.add('active');
        }
    }

    async loadProviders() {
        try {
            const response = await fetch('/api/providers');
            const data = await response.json();

            const channelProviderSelect = document.getElementById('channel_provider');
            if (channelProviderSelect) {
                data.providers.forEach(provider => {
                    const option = document.createElement('option');
                    option.value = provider.id;
                    option.textContent = provider.name;
                    channelProviderSelect.appendChild(option);
                });
            }
        } catch (error) {
            console.error('Failed to load providers:', error);
        }
    }

    async loadCapabilities() {
        try {
            const response = await fetch('/api/capabilities');
            await response.json();
            // 处理能力选项
        } catch (error) {
            console.error('Failed to load capabilities:', error);
        }
    }

    async startDetection() {
        const form = document.getElementById('detectionForm');
        const formData = new FormData(form);

        // 获取选中的能力
        const capabilities = Array.from(document.querySelectorAll('input[name="capabilities"]:checked'))
            .map(cb => cb.value);

        const requestData = {
            provider: formData.get('provider'),
            base_url: formData.get('base_url'),
            api_key: formData.get('api_key'),
            timeout: parseInt(formData.get('timeout')),
            target_model: formData.get('target_model') || formData.get('target_model_select'),
            capabilities: capabilities.length > 0 ? capabilities : null
        };

        try {
            const response = await fetch('/api/detect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(requestData)
            });

            const result = await response.json();

            if (response.ok) {
                this.currentTaskId = result.task_id;
                this.showProgress();
                this.startProgressPolling();
            } else {
                this.showError('检测启动失败: ' + (result.detail || '未知错误'));
            }
        } catch (error) {
            this.showError('请求失败: ' + error.message);
        }
    }

    showProgress() {
        document.getElementById('progress').style.display = 'block';
        document.getElementById('results').style.display = 'none';

        // 重置进度条
        const progressFill = document.getElementById('progress-fill');
        const progressText = document.getElementById('progress-text');
        progressFill.style.width = '0%';
        progressText.textContent = '准备中...';
    }

    startProgressPolling() {
        this.progressInterval = setInterval(() => {
            this.checkProgress();
        }, 1000);
    }

    stopProgressPolling() {
        if (this.progressInterval) {
            clearInterval(this.progressInterval);
            this.progressInterval = null;
        }
    }

    async checkProgress() {
        if (!this.currentTaskId) return;

        try {
            const response = await fetch(`/api/progress/${this.currentTaskId}`);
            const progress = await response.json();

            this.updateProgress(progress);

            if (progress.status === 'completed') {
                this.stopProgressPolling();
                await this.loadResults();
            } else if (progress.status === 'error') {
                this.stopProgressPolling();
                this.showError('检测过程中发生错误: ' + (progress.error || '未知错误'));
            }
        } catch (error) {
            console.error('获取进度失败:', error);
        }
    }

    updateProgress(progress) {
        const progressFill = document.getElementById('progress-fill');
        const progressText = document.getElementById('progress-text');

        progressFill.style.width = progress.progress + '%';

        if (progress.status === 'starting') {
            progressText.textContent = '正在启动检测...';
        } else if (progress.status === 'running') {
            if (progress.current_capability) {
                progressText.textContent = `正在检测: ${progress.current_capability}`;
            } else {
                progressText.textContent = '检测中...';
            }
        } else if (progress.status === 'waiting') {
            progressText.textContent = `⏱️ ${progress.current_capability || '等待中...'}`;
        } else if (progress.status === 'completed') {
            progressText.textContent = '检测完成！';
        }
    }

    async loadResults() {
        if (!this.currentTaskId) return;

        try {
            const response = await fetch(`/api/results/${this.currentTaskId}`);
            const results = await response.json();

            this.displayResults(results);
            
            // 清除任务ID，防止重复请求
            this.currentTaskId = null;
        } catch (error) {
            console.error('加载结果失败:', error);
            this.showError('加载结果失败: ' + error.message);
        }
    }

    displayResults(results) {
        const resultsSection = document.getElementById('results');
        const resultsContent = document.getElementById('results-content');

        // 计算统计信息
        const totalCapabilities = Object.keys(results.capabilities).length;
        const supportedCapabilities = Object.values(results.capabilities)
            .filter(cap => cap.status === 'supported').length;

        // 生成结果HTML
        resultsContent.innerHTML = `
            <div class="results-summary fade-in">
                <div class="summary-card">
                    <h3>支持的能力</h3>
                    <div class="value">${supportedCapabilities}/${totalCapabilities}</div>
                </div>
                <div class="summary-card">
                    <h3>检测提供商</h3>
                    <div class="value">${results.provider.toUpperCase()}</div>
                </div>
                <div class="summary-card">
                    <h3>检测状态</h3>
                    <div class="value">✅ 完成</div>
                </div>
            </div>

            <div class="capabilities-results fade-in">
                <h3>能力检测详情</h3>
                <table class="capabilities-table">
                    <thead>
                        <tr>
                            <th>能力</th>
                            <th>状态</th>
                            <th>响应时间</th>
                            <th>详情</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${this.generateCapabilityRows(results.capabilities)}
                    </tbody>
                </table>
            </div>

            <div class="detection-info fade-in">
                <p><strong>检测时间:</strong> ${new Date(results.detection_time).toLocaleString()}</p>
                <p><strong>模型:</strong> ${results.models.join(', ')}</p>
                <p><strong>API基础URL:</strong> ${results.base_url}</p>
            </div>
        `;

        // 隐藏进度条，显示结果
        document.getElementById('progress').style.display = 'none';
        resultsSection.style.display = 'block';

        // 添加淡入动画
        setTimeout(() => {
            resultsSection.classList.add('fade-in');
        }, 100);
    }

    generateCapabilityRows(capabilities) {
        return Object.entries(capabilities).map(([capabilityId, result]) => {
            const statusClass = result.status === 'supported' ? 'status-supported' : 'status-not-supported';
            const statusText = result.status === 'supported' ? '✅ 支持' : '❌ 不支持';
            const responseTime = result.response_time ? `${result.response_time.toFixed(2)}ms` : 'N/A';

            return `
                <tr>
                    <td>${capabilityId}</td>
                    <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                    <td>${responseTime}</td>
                    <td>
                        ${result.error ? `<span class="error-text">${result.error}</span>` : ''}
                        ${result.details ? `<pre class="details-text">${JSON.stringify(result.details, null, 2)}</pre>` : ''}
                    </td>
                </tr>
            `;
        }).join('');
    }

    showError(message) {
        // 创建错误提示
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-message fade-in';
        errorDiv.textContent = message;

        // 插入到页面顶部
        const container = document.querySelector('.container');
        container.insertBefore(errorDiv, container.firstChild);

        // 3秒后自动消失
        setTimeout(() => {
            errorDiv.remove();
        }, 3000);
    }

    setupModernAnimations() {
        // 动画设置
        const inputs = document.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
            input.addEventListener('focus', () => {
                input.closest('.form-group').classList.add('focused');
            });

            input.addEventListener('blur', () => {
                input.closest('.form-group').classList.remove('focused');
            });
        });
    }

    // 渠道管理方法
    async createChannel() {
        const form = document.getElementById('channelForm');
        const formData = new FormData(form);
        const channelData = Object.fromEntries(formData.entries());

        // 转换数值类型
        channelData.timeout = parseInt(channelData.timeout);
        channelData.max_retries = parseInt(channelData.max_retries);

        try {
            const sessionToken = localStorage.getItem('session_token');
            const response = await fetch('/api/channels', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${sessionToken}`
                },
                body: JSON.stringify(channelData)
            });

            const result = await response.json();

            if (response.ok) {
                alert('渠道创建成功！');
                form.reset();
                await this.loadChannels(); // 重新加载渠道列表
            } else {
                alert('创建失败: ' + (result.detail || '未知错误'));
            }
        } catch (error) {
            alert('请求失败: ' + error.message);
        }
    }

    async loadChannels() {
        console.log('开始加载渠道列表...');
        try {
            const sessionToken = localStorage.getItem('session_token');
            const response = await fetch('/api/channels', {
                headers: {
                    'Authorization': `Bearer ${sessionToken}`
                }
            });
            const result = await response.json();
            console.log('API响应:', result);

            if (response.ok) {
                this.channels = result.channels;
                console.log('渠道数量:', this.channels.length);
                this.updateChannelsList();
            } else {
                console.error('加载渠道列表失败:', result.detail);
                // 显示错误信息
                const channelsList = document.getElementById('channelsList');
                if (channelsList) {
                    channelsList.innerHTML = '<p>加载渠道列表失败</p>';
                }
            }
        } catch (error) {
            console.error('加载渠道列表失败:', error);
            // 显示错误信息
            const channelsList = document.getElementById('channelsList');
            if (channelsList) {
                channelsList.innerHTML = '<p>加载渠道列表失败</p>';
            }
        }
    }

    updateChannelsList() {
        console.log('更新渠道列表，渠道数量:', this.channels.length);
        const channelsList = document.getElementById('channelsList');
        if (!channelsList) {
            console.error('找不到channelsList元素');
            return;
        }

        if (this.channels.length === 0) {
            console.log('渠道列表为空，显示暂无配置信息');
            channelsList.innerHTML = '<p>暂无配置的渠道</p>';
            return;
        }

        const channelsHTML = this.channels.map(channel => `
            <div class="channel-item ${channel.enabled ? 'enabled' : 'disabled'}">
                <div class="channel-info">
                    <h4>${channel.name}</h4>
                    <p><strong>提供商:</strong> ${channel.provider}</p>
                    <p><strong>URL:</strong> ${channel.base_url}</p>
                    <p><strong>自定义Key:</strong> <code>${channel.custom_key}</code></p>
                    <p><strong>状态:</strong> ${channel.enabled ? '启用' : '禁用'}</p>
                    <p><small>创建时间: ${new Date(channel.created_at).toLocaleString()}</small></p>
                </div>
                <div class="channel-actions">
                    <button onclick="apiConverter.testChannel('${channel.id}')" class="btn-secondary">测试</button>
                    <button onclick="apiConverter.editChannel('${channel.id}')" class="btn-primary">编辑</button>
                    <button onclick="apiConverter.toggleChannel('${channel.id}')" class="btn-secondary">
                        ${channel.enabled ? '禁用' : '启用'}
                    </button>
                    <button onclick="apiConverter.deleteChannel('${channel.id}')" class="btn-danger">删除</button>
                </div>
            </div>
        `).join('');

        channelsList.innerHTML = channelsHTML;
    }

    async testChannel(channelId) {
        try {
            const response = await fetch(`/api/channels/${channelId}/test`);
            const result = await response.json();

            if (response.ok) {
                alert(`测试成功！响应时间: ${result.test_result.response_time}ms`);
            } else {
                alert('测试失败: ' + (result.detail || '未知错误'));
            }
        } catch (error) {
            alert('测试失败: ' + error.message);
        }
    }

    async toggleChannel(channelId) {
        const channel = this.channels.find(c => c.id === channelId);
        if (!channel) return;

        try {
            const response = await fetch(`/api/channels/${channelId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    enabled: !channel.enabled
                })
            });

            const result = await response.json();

            if (response.ok) {
                this.loadChannels(); // 重新加载渠道列表
            } else {
                alert('操作失败: ' + (result.detail || '未知错误'));
            }
        } catch (error) {
            alert('操作失败: ' + error.message);
        }
    }

    editChannel(channelId) {
        const channel = this.channels.find(c => c.id === channelId);
        if (!channel) {
            alert('渠道不存在');
            return;
        }

        // 修改表单标题和按钮
        document.querySelector('.channel-form h3').textContent = '编辑渠道';
        const submitButton = document.querySelector('#channelForm button[type="submit"]');
        submitButton.textContent = '更新渠道';
        
        // 存储当前编辑的渠道ID到表单数据属性
        const form = document.getElementById('channelForm');
        form.dataset.editingChannelId = channelId;
        form.dataset.isEditing = 'true';
        
        // 填充表单数据
        document.getElementById('name').value = channel.name || '';
        document.getElementById('channel_provider').value = channel.provider || '';
        document.getElementById('base_url').value = channel.base_url || '';
        // 对于API密钥，如果已掩码则设置placeholder，否则清空
        const apiKeyInput = document.getElementById('api_key');
        if (channel.api_key && channel.api_key.includes('***')) {
            apiKeyInput.value = '';
            apiKeyInput.placeholder = channel.api_key + ' (留空保持不变)';
            apiKeyInput.required = false;  // 编辑时可以不填
        } else {
            apiKeyInput.value = '';
            apiKeyInput.placeholder = '输入新的API密钥或留空保持不变';
            apiKeyInput.required = false;  // 编辑时可以不填
        }
        document.getElementById('custom_key').value = channel.custom_key || '';
        document.getElementById('timeout').value = channel.timeout || 30;
        document.getElementById('max_retries').value = channel.max_retries || 3;

        // 添加取消按钮
        if (!document.getElementById('cancelEdit')) {
            const cancelButton = document.createElement('button');
            cancelButton.id = 'cancelEdit';
            cancelButton.type = 'button';
            cancelButton.className = 'btn-secondary';
            cancelButton.textContent = '取消编辑';
            cancelButton.onclick = () => this.cancelEdit();
            submitButton.parentNode.insertBefore(cancelButton, submitButton.nextSibling);
        }

        // 添加编辑状态样式提示
        const channelForm = document.querySelector('.channel-form');
        if (channelForm) {
            channelForm.classList.add('editing-mode');
            
            // 延迟滚动，确保DOM更新完成
            setTimeout(() => {
                channelForm.scrollIntoView({ 
                    behavior: 'smooth', 
                    block: 'start',
                    inline: 'nearest'
                });
            }, 100);
        }
    }

    cancelEdit() {
        // 恢复表单标题和按钮
        document.querySelector('.channel-form h3').textContent = '添加新渠道';
        const submitButton = document.querySelector('#channelForm button[type="submit"]');
        submitButton.textContent = '添加渠道';
        
        // 清除编辑状态
        const form = document.getElementById('channelForm');
        delete form.dataset.editingChannelId;
        delete form.dataset.isEditing;
        
        // 重置表单
        form.reset();
        
        // 重置API密钥字段的placeholder和必填属性
        const apiKeyInput = document.getElementById('api_key');
        apiKeyInput.placeholder = '';
        apiKeyInput.required = true;

        // 移除取消按钮
        const cancelButton = document.getElementById('cancelEdit');
        if (cancelButton) {
            cancelButton.remove();
        }

        // 移除编辑状态样式
        const channelForm = document.querySelector('.channel-form');
        if (channelForm) {
            channelForm.classList.remove('editing-mode');
        }
    }

    async updateChannel(channelId) {
        const form = document.getElementById('channelForm');
        const formData = new FormData(form);
        const channelData = Object.fromEntries(formData.entries());

        // 转换数值类型
        channelData.timeout = parseInt(channelData.timeout);
        channelData.max_retries = parseInt(channelData.max_retries);

        // 输入验证
        if (!channelData.name || !channelData.provider || !channelData.base_url || !channelData.custom_key) {
            alert('请填写所有必填字段');
            return;
        }
        
        // 如果API密钥是掩码形式或为空，则不更新密钥
        if (!channelData.api_key || channelData.api_key.trim() === '' || channelData.api_key.includes('***')) {
            delete channelData.api_key;
        }
        
        try {
            const response = await fetch(`/api/channels/${channelId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(channelData)
            });

            const result = await response.json();

            if (response.ok) {
                alert('渠道更新成功！');
                this.cancelEdit();
                await this.loadChannels(); // 重新加载渠道列表
            } else {
                if (response.status === 404) {
                    alert('渠道不存在，可能已被删除。页面将刷新以同步最新数据。');
                    await this.loadChannels();
                    this.cancelEdit();
                } else {
                    alert('更新失败: ' + (result.detail || '未知错误'));
                }
            }
        } catch (error) {
            alert('请求失败: ' + error.message);
        }
    }

    async deleteChannel(channelId) {
        if (!confirm('确定要删除这个渠道吗？')) return;

        try {
            const response = await fetch(`/api/channels/${channelId}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const result = await response.json();

            if (response.ok) {
                alert('删除成功！');
                await this.loadChannels(); // 重新加载渠道列表
            } else {
                alert('删除失败: ' + (result.detail || '未知错误'));
            }
        } catch (error) {
            alert('删除失败: ' + error.message);
        }
    }
}

// 现代化的平滑滚动效果
class SmoothScroll {
    constructor() {
        this.init();
    }

    init() {
        // 为链接添加平滑滚动
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', (e) => {
                e.preventDefault();
                const target = document.querySelector(anchor.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }
}

// 全局函数定义
function updateTargetModel() {
    const select = document.getElementById('target_model_select');
    const input = document.getElementById('target_model');
    if (select && input && select.value) {
        input.value = select.value;
    }
}

function selectAllCapabilities() {
    document.querySelectorAll('input[name="capabilities"]').forEach(cb => {
        cb.checked = true;
    });
}

function clearAllCapabilities() {
    document.querySelectorAll('input[name="capabilities"]').forEach(cb => {
        cb.checked = false;
    });
}

async function fetchModels() {
    const provider = document.getElementById('provider').value;
    const baseUrl = document.getElementById('detection_base_url').value;
    const apiKey = document.getElementById('detection_api_key').value;

    if (!provider || !baseUrl || !apiKey) {
        alert('请先填写提供商、API基础URL和API密钥');
        return;
    }

    const btn = document.getElementById('fetch_models_btn');
    btn.disabled = true;
    btn.textContent = '获取中...';

    try {
        const response = await fetch('/api/fetch_models', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                provider: provider,
                base_url: baseUrl,
                api_key: apiKey
            })
        });

        const data = await response.json();

        if (response.ok) {
            const select = document.getElementById('target_model_select');
            select.innerHTML = '<option value="">请选择模型</option>';

            data.models.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                select.appendChild(option);
            });
        } else {
            alert('获取模型列表失败: ' + (data.detail || '未知错误'));
        }
    } catch (error) {
        alert('请求失败: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '获取模型列表';
    }
}

// 全局实例
let apiConverter;

// 初始化应用函数
function initializeApp() {
    apiConverter = new APIConverter();
    new SmoothScroll();

    // 添加键盘导航支持
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Tab') {
            document.body.classList.add('keyboard-navigation');
        }
    });

    document.addEventListener('mousedown', () => {
        document.body.classList.remove('keyboard-navigation');
    });
}

// 如果页面已经加载完成，直接初始化；否则等待DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
} else {
    initializeApp();
}