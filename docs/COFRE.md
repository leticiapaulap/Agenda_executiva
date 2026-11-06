# Cofre de Senhas — configuração e operação

## Escopo

A análise do catálogo anterior confirmou que não existia serviço de criptografia nem chave de cofre. Esta atualização acrescenta `agenda/vault.py`, compartilhado pelo desktop e pela web, sem usar `SECRET_KEY`. As outras seções da agenda mantêm suas regras.

O campo **Senha é opcional**. Na edição ele começa vazio: deixar vazio mantém a senha atual; preencher substitui; a opção explícita de remoção apaga somente a senha. Salvar/substituir/remover exige reautenticação na interface, assim como revelar ou copiar. A senha nunca é preenchida no HTML inicial ou nas consultas de listagem/edição.

Esta versão atende **acessos pessoais autorizados de usuários que receberam permissão para o cofre**. Registros antigos migram como **Corporativo / somente catálogo**, sem senha. Para um acesso pessoal, selecione explicitamente **Pessoal · armazenamento autorizado**. Acessos corporativos continuam sem armazenamento de senha, independentemente das chaves configuradas. Não reclassifique uma credencial corporativa como pessoal para contornar essa política. Suporte corporativo exigirá autorização e avaliação específica da organização antes de ser implementado.

Usuário/e-mail não existiam no catálogo deste checkout e não foram adicionados como campos sensíveis nesta alteração. Não coloque credenciais em nome, URL, categoria ou observações. A URL continua limitada a HTTP/HTTPS, sem usuário, senha, parâmetros ou fragmentos.

## Variáveis no Render

Não altere `SECRET_KEY` nem `DATABASE_URL` para configurar o cofre. `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` são usados somente no bootstrap inicial; as contas persistidas e seus hashes ficam no PostgreSQL. A reautenticação web verifica a senha atual do usuário autorizado, não uma senha global do gestor.

| Nova variável | Valor necessário |
|---|---|
| `VAULT_ENABLED` | `true` somente após autorizar o armazenamento de acessos pessoais; padrão `false` |
| `VAULT_KEYS` | JSON com versões e chaves aleatórias de **32 bytes**, codificadas em Base64 URL-safe: objeto no formato `{"v1":"<chave gerada>"}` |
| `VAULT_ACTIVE_KEY_ID` | Identificador da chave para novas gravações, inicialmente `v1`; deve existir no JSON |

Não há chave padrão. A codificação Base64 serve para transportar os bytes da chave/ciphertext; **as senhas são criptografadas com AES-256-GCM**, nonce aleatório de 12 bytes por gravação, tag autenticada e dados associados vinculando registro e versão da chave. Alterar ciphertext, nonce, chave ou registro impede a descriptografia. Referência técnica: [AES-GCM na documentação cryptography](https://cryptography.io/en/50.0.2/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).

Gere o JSON **localmente, em terminal privado**, com o ambiente virtual atualizado:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "import base64,json,secrets; print(json.dumps({'v1': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')}))"
```

O resultado é um segredo real: copie somente para `VAULT_KEYS` em **Environment** do serviço web no Render e para sua custódia de recuperação cifrada. Não execute o gerador em build/pre-deploy/logs do Render; não envie o resultado ao chat, Git, tickets ou arquivos rastreados. Restrinja acesso ao painel e ao processo de execução. Mantenha uma cópia cifrada das chaves, com controle de acesso, **separada do backup do banco**. Não gere uma chave nova a cada deploy. Cada ambiente (produção, homologação, desktop) deve ter chaves próprias.

Se `VAULT_ENABLED` estiver ausente/desativado ou as chaves forem ausentes, inválidas ou incompletas, o catálogo permanece disponível e os controles de senha ficam desabilitados. Nenhum dado cifrado é descartado automaticamente. O aplicativo não lê `.env` sozinho; no Render configure Environment, e no desktop injete as variáveis no processo por meio do mecanismo seguro escolhido pelo operador.

## Desktop

Além das três variáveis acima, o desktop exige **`VAULT_DESKTOP_PASSWORD_HASH`**, contendo hash Werkzeug scrypt ou PBKDF2 de uma senha de autenticação local. Essa variável **não é necessária no Render**. Gere o hash sem mostrar a senha:

```powershell
.\.venv\Scripts\python.exe -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Senha de autenticação local do cofre: ')))"
```

Configure o hash e o conjunto de chaves no ambiente do processo desktop, restrito à conta Windows autorizada. Sem o hash, o desktop mantém somente catálogo, mesmo com chaves válidas. As chaves não são derivadas da senha de autenticação; alterar essa senha não rotaciona as chaves. A proteção depende também das permissões Windows, proteção do disco e segurança da sessão do usuário; processos executados sob a mesma conta podem ter acesso a ambiente/memória. Não há isolamento contra um administrador do sistema comprometido.

## Revelação, cópia e falhas

Na web, cada operação `POST /vault/<id>/secret` exige sessão autenticada e recente, token CSRF e senha atual do usuário autorizado. Aceita `action=reveal` ou `action=copy`. É a **única resposta HTTP que devolve a senha em texto puro**, para o navegador autorizado realizar a ação. Não existe listagem JSON de senhas. Login/CSRF são exigidos também nos cadastros e exclusões. A senha de reautenticação não é guardada na sessão ou no banco.

Cinco falhas de reautenticação bloqueiam novas tentativas por 15 minutos, com estado persistido por usuário no banco, independentemente do endereço de origem e de reiniciar o serviço. O desktop possui controle equivalente persistido em `settings`. Um novo login não constitui dispensa de reautenticação do cofre.

As respostas do cofre usam `Cache-Control: no-store`, `Pragma: no-cache` e `Referrer-Policy: strict-origin-when-cross-origin`. Essa política mantém o `Referer` de mesma origem necessário à validação CSRF em HTTPS, sem enviar caminhos completos a outros domínios. A interface usa `textContent`, não injeta HTML contendo senha. A exibição é mascarada após 30 segundos e ao deixar a página/seção. Campos de autenticação são limpos ao concluir/cancelar. Falhas de autenticação, permissões, configuração, integridade ou descriptografia apresentam mensagens fixas sem chaves, ciphertexts, formulários ou senhas nos logs.

Na web, copiar depende de HTTPS e da permissão do navegador para a área de transferência. A limpeza após 30 segundos só é tentada se já houver permissão de leitura e se o conteúdo ainda coincidir com a senha copiada; o aplicativo não solicita automaticamente leitura nem apaga conteúdo alheio. No desktop a limpeza também verifica o conteúdo antes de apagá-lo. Histórico do clipboard, sincronização do sistema e gerenciadores externos podem conservar cópias; a aplicação não os controla. A ocultação também não apaga capturas de tela nem garante zerar toda a memória do runtime.

Se uma chave antiga estiver ausente ou os dados forem adulterados, a revelação falha de forma genérica. Metadados continuam editáveis: campo de nova senha vazio **preserva exatamente o ciphertext anterior**. Restaure a versão correta da chave pela custódia de recuperação; não substitua o banco nem gere uma chave esperando recuperar dados antigos. Perder todas as cópias de uma chave torna as senhas dependentes dela irrecuperáveis.

## Migração e publicação

- Dependência nova: `cryptography==50.0.2`.
- Nova revisão Alembic: **`0004_vault_passwords`**, após `0003_access_catalog`. Adiciona `password_ciphertext` anulável e `is_corporate` com padrão `1`, além de restrições que impedem senha em registro corporativo. Não recria nem remove acessos/compromissos.
- Desktop: SQLite passa de esquema 3 para **4**, com backup automático antes da migração aditiva. Os IDs e dados dos acessos existentes são preservados. Backups cifrados continuam exigindo suas chaves originais.

Após backup e revisão do código, envie as alterações ao branch conectado ao Render. Preserve build `pip install -r requirements.txt`, pre-deploy **`flask --app app db upgrade`** e start `gunicorn app:app`. Quem já está em `0003` executará apenas `0004`; instalações anteriores aplicarão também as revisões pendentes. Confirme `flask --app app db current`. Não execute downgrade automático.

Em homologação, confira login, catálogo corporativo, senha pessoal, máscara inicial, reautenticação, bloqueio de tentativas, edição vazia, recuperação da chave e preservação das outras seções. PostgreSQL/Render reais ainda precisam de homologação; não foram acessados nesta implementação. A geração do novo executável desktop continua pelo build do README.

## Rotação e recuperação

1. Faça backup do banco e preserve cópia cifrada de todas as chaves atuais em custódia separada. Gere outra chave aleatória de 32 bytes usando o mesmo comando, escolhendo um identificador novo, por exemplo `v2`.
2. Adicione `v2` a `VAULT_KEYS`, **mantendo `v1` e todas as versões necessárias**. Altere `VAULT_ACTIVE_KEY_ID` para `v2` e reinicie/republique o processo. Novas gravações usam a chave nova; registros antigos continuam legíveis com suas versões originais.
3. Valide todas as senhas no Render sem modificar dados:

   ```text
   flask --app app vault-rotate
   ```

4. Aplique a rotação explicitamente após verificar o backup:

   ```text
   flask --app app vault-rotate --apply
   ```

   O comando bloqueia as linhas, recriptografa todas as senhas numa transação e só confirma ao terminar. Se qualquer chave/registro falhar, não confirma alteração alguma. O console mostra apenas contagens e mensagens fixas. Faça a operação em janela de manutenção para evitar novas gravações enquanto verifica o resultado.

5. No desktop, feche a interface e indique **explicitamente** o arquivo existente:

   ```powershell
   .\.venv\Scripts\python.exe -m agenda.vault_admin rotate --database "$env:USERPROFILE\AgendaExecutiva\agenda.db"
   .\.venv\Scripts\python.exe -m agenda.vault_admin rotate --database "$env:USERPROFILE\AgendaExecutiva\agenda.db" --apply
   ```

   Sem `--apply`, somente valida. Com `--apply`, cria backup anterior e aplica em transação SQLite exclusiva para gravação. O comando não cria banco por erro de caminho e não migra esquema silenciosamente.

6. Confira descriptografia e restauração em ambiente isolado antes de retirar qualquer chave. Não remova versões antigas enquanto backups retidos ainda dependerem delas. Restrinja a distribuição das chaves aos operadores autorizados e registre a data/versão da rotação, sem copiar chaves ou senhas para a auditoria.

## Testes e arquivos

`tests/test_vault.py` cobre criptografia, adulteração, associação ao registro, política corporativa, perda/rotação de chaves, CRUD, migrações desktop/web, autenticação, CSRF, sessão recente, bloqueio de tentativas e rotação web atômica. `tests/test_vault_admin.py` testa a ferramenta desktop, backup e rollback. `tests/smoke_vault.py` exercita os controles reais CustomTkinter. `tests/smoke_vault_web.py` usa Edge headless e Playwright (dependência opcional em `requirements-test.txt`), com clipboard real, avanço de relógio para testar 30 segundos e viewport de 390 px. A suíte usa somente chaves aleatórias de teste e bancos temporários.

Arquivos deste aprimoramento: `agenda/vault.py`, `agenda/vault_admin.py`, `agenda/catalog.py`, `agenda/storage.py`, `agenda/feature_views.py`, `agenda/ui.py`, `webapp/catalog.py`, `webapp/vault_commands.py`, `webapp/__init__.py`, `webapp/templates/catalog.html`, `webapp/static/vault.js`, estilos do cofre em `webapp/static/style.css`, `migrations/versions/0004_vault_passwords.py`, testes citados e ajustes de expectativas nos testes antigos de catálogo. Também foram atualizados `requirements.txt`, `requirements-test.txt`, exemplo de ambiente, README e documentação. Sem chave real versionada ou alteração de variáveis de produção.
