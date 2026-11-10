# Cofre de Senhas — configuração e operação

## Escopo

A seção web **Senhas** substitui Tarefas no menu e reutiliza o cofre existente: a tabela `access_catalog`, o serviço AES-GCM de `agenda/vault.py` e o chaveiro externo já configurado. Não cria outro armazenamento nem troca a chave. `SECRET_KEY` continua destinada exclusivamente às sessões. Usuários, compromissos e tarefas existentes são preservados.

O campo **Senha é opcional**. Na edição ele começa vazio: deixar vazio mantém a senha atual; preencher substitui; a opção explícita de remoção apaga somente a senha. Salvar/substituir/remover exige reautenticação na interface, assim como revelar ou copiar. A senha nunca é preenchida no HTML inicial ou nas consultas de listagem/edição.

A versão web atende **acessos pessoais e corporativos de usuários com permissão explícita para o cofre**. A habilitação de senhas corporativas foi autorizada para esta atualização. Ambos os tipos usam a mesma tabela, criptografia e reautenticação; nenhum perfil recebe acesso automaticamente. A classificação dos acessos e as senhas já cifradas são preservadas. Registros corporativos existentes continuam sem senha até uma gravação explícita e reautenticada; não há preenchimento ou conversão automática. O desktop independente mantém sua política anterior de senhas somente pessoais.

O formulário contém serviço/sistema, usuário/e-mail, senha, URL, categoria e observações. Usuário/e-mail são metadados visíveis aos usuários autorizados do cofre e podem ser pesquisados e copiados. Senhas pertencem somente ao campo próprio; não as coloque em nome, URL, categoria ou observações. A URL continua limitada a HTTP/HTTPS, sem usuário, senha, parâmetros ou fragmentos.

## Variáveis no Render

Não altere `SECRET_KEY` nem `DATABASE_URL` para configurar o cofre. `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` são usados somente no bootstrap inicial; as contas persistidas e seus hashes ficam no PostgreSQL. A reautenticação web verifica a senha atual do usuário autorizado, não uma senha global do gestor.

| Variável existente do cofre | Valor necessário |
|---|---|
| `VAULT_ENABLED` | `true` para habilitar o armazenamento autorizado de senhas; padrão `false` |
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

Excluir abre uma página de confirmação com o nome do serviço, sem descriptografar a senha. **Cancelar** apenas retorna à lista. A exclusão exige `POST /vault/<id>/delete`, CSRF válido, permissão para o cofre e confirmação explícita (`confirm=1`); um GET nunca exclui e um POST sem confirmação é recusado com HTTP 400. Esse fluxo funciona também sem JavaScript.

Cinco falhas de reautenticação bloqueiam novas tentativas por 15 minutos, com estado persistido por usuário no banco, independentemente do endereço de origem e de reiniciar o serviço. O desktop possui controle equivalente persistido em `settings`. Um novo login não constitui dispensa de reautenticação do cofre.

As respostas do cofre usam `Cache-Control: no-store`, `Pragma: no-cache` e `Referrer-Policy: strict-origin-when-cross-origin`. Essa política mantém o `Referer` de mesma origem necessário à validação CSRF em HTTPS, sem enviar caminhos completos a outros domínios. A interface usa `textContent`, não injeta HTML contendo senha. A exibição é mascarada após 30 segundos e ao deixar a página/seção. Campos de autenticação são limpos ao concluir/cancelar. Falhas de autenticação, permissões, configuração, integridade ou descriptografia apresentam mensagens fixas sem chaves, ciphertexts, formulários ou senhas nos logs.

Na web, copiar depende de HTTPS e da permissão do navegador para a área de transferência. A limpeza após 30 segundos só é tentada se já houver permissão de leitura e se o conteúdo ainda coincidir com a senha copiada; o aplicativo não solicita automaticamente leitura nem apaga conteúdo alheio. No desktop a limpeza também verifica o conteúdo antes de apagá-lo. Histórico do clipboard, sincronização do sistema e gerenciadores externos podem conservar cópias; a aplicação não os controla. A ocultação também não apaga capturas de tela nem garante zerar toda a memória do runtime.

Se uma chave antiga estiver ausente ou os dados forem adulterados, a revelação falha de forma genérica. Metadados continuam editáveis: campo de nova senha vazio **preserva exatamente o ciphertext anterior**. Restaure a versão correta da chave pela custódia de recuperação; não substitua o banco nem gere uma chave esperando recuperar dados antigos. Perder todas as cópias de uma chave torna as senhas dependentes dela irrecuperáveis.

## Migração e publicação

- Dependência do cofre existente: `cryptography==50.0.2`.
- A revisão anterior **`0004_vault_passwords`**, após `0003_access_catalog`, adicionou `password_ciphertext` anulável e `is_corporate` com padrão `1`, inicialmente limitando senhas a registros pessoais.
- A interface web **Senhas** reaproveita `access_catalog`; não há um segundo cofre. A revisão aditiva **`0008_access_username`** acrescenta `username` vazio para registros existentes, mantendo suas senhas cifradas e metadados. O menu abre a página atual do cofre, com cadastro, edição, busca, cópia de usuário, link do site e exclusão confirmada. Tarefas antigas permanecem na tabela `tasks` e têm página administrativa somente leitura.
- A revisão **`0009_corporate_passwords`** autoriza o mesmo armazenamento criptografado para acessos corporativos na web. No PostgreSQL, remove somente a restrição `ck_access_catalog_password_scope`; mantém colunas, classificação, IDs, índices, chaves e todas as linhas. Não atualiza ou apaga dados nem recria tabelas. A restrição de classificação válida (`0`/`1`) permanece. O SQLite usado nos testes web reconstrói somente `access_catalog`, copiando todas as linhas; isso não é executado no PostgreSQL nem no SQLite desktop.
- A URL antiga `/tasks` redireciona para `/vault`. Os POSTs antigos de tarefas respondem com `410 Gone`, sem gravação; a consulta administrativa é `/tasks/archive`. Pesquisa, calendário e lembretes continuam permitindo consultar tarefas antigas conforme as permissões existentes. Nenhuma tarefa é convertida em acesso.
- Desktop: SQLite passa de esquema 4 para **5**, com backup automático antes da migração aditiva de `username`. Os IDs e demais dados dos acessos existentes são preservados. Backups cifrados continuam exigindo suas chaves originais.

Após backup e revisão do código, envie as alterações ao branch conectado ao Render. Preserve build `pip install -r requirements.txt`, pre-deploy **`flask --app app db upgrade`** e start `gunicorn app:app`. Quem já está em `0008_access_username` executará somente `0009_corporate_passwords`; instalações anteriores aplicarão também as revisões pendentes. Confirme `flask --app app db current` como `0009_corporate_passwords`. Não execute downgrade automático. Não é necessária variável de ambiente nova: mantenha as mesmas `VAULT_ENABLED`, `VAULT_KEYS` e `VAULT_ACTIVE_KEY_ID`; não troque nem regenere as chaves durante a atualização.

Em homologação, confira login, senhas pessoais e corporativas, máscara inicial, reautenticação, bloqueio de tentativas, edição vazia, recuperação da chave e preservação das outras seções. A integração automatizada usa PostgreSQL local descartável; ela não acessa o banco do Render nem substitui a validação autenticada do serviço publicado. A geração do executável desktop continua pelo build do README.

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

Os testes de cofre validam cadastro, edição, exclusão, pesquisa por usuário, autorização, reautenticação e criptografia; `tests/smoke_vault_web.py` executa os fluxos pessoais e corporativos, inclusive cópia do usuário e exclusão confirmada sem JavaScript. `tests/test_password_migration.py` verifica as atualizações `0007` → `0008` → `0009`: preservação de todas as linhas, manutenção do ciphertext e da chave original, classificação dos acessos antigos, restrições remanescentes e ausência de segredos em erros ou logs.

`tests/test_password_postgres.py` executa a migração e o fluxo web em PostgreSQL real quando `TEST_POSTGRES_DATABASE_URL` aponta explicitamente para um **banco descartável cujo nome contenha `test`**. O teste cria um esquema exclusivo e o remove ao terminar; nunca use a URL de produção nessa variável. Ele confere preservação dos registros e do ciphertext original, criptografia de novos acessos, edição com senha vazia, persistência em uma segunda instância Flask, pesquisa, exclusão confirmada, permissões e CSRF em HTTPS. Sem essa variável, o teste é omitido. A variável é exclusiva dos testes e **não deve ser adicionada ao serviço de produção no Render**.

Para executar somente essa integração no ambiente de testes já configurado:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_password_postgres -v
```
