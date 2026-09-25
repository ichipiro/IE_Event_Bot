# Changelog

## [0.6.0](https://github.com/ichipiro/IE_Event_Bot/compare/v0.5.1...v0.6.0) (2026-09-25)


### Features

* **e2e:** 7件のページ送りと部分失敗の再試行を検証 ([3eef095](https://github.com/ichipiro/IE_Event_Bot/commit/3eef09528767ff966f7df09901066934845c3f81))
* **e2e:** calendar開始条件の読取診断を追加 ([83cd4b3](https://github.com/ichipiro/IE_Event_Bot/commit/83cd4b3f8b5585935a9c42721e94f68e393fa0f6))
* **e2e:** google全件適用と共有KVの検証を追加 ([ea6044e](https://github.com/ichipiro/IE_Event_Bot/commit/ea6044e63dd329aebc95d55ed789cf4674519497))
* **e2e:** 作成通知の所有と再試行を検証 ([2b0bb18](https://github.com/ichipiro/IE_Event_Bot/commit/2b0bb18138a1beaa369e3bb1eed5dd6b362b4b28))
* **e2e:** 全体同期の往復と部分失敗を検証 ([a2b1af5](https://github.com/ichipiro/IE_Event_Bot/commit/a2b1af5b657099627db074e0c2c97c335b718491))
* **e2e:** 外部fixtureと通常KVの所有権を接続 ([727a700](https://github.com/ichipiro/IE_Event_Bot/commit/727a7008a2adfd0842c82eb1f9124acb3e0cf188))
* **e2e:** 所有イベントを通常ポーリングへ接続 ([a5cc0ad](https://github.com/ichipiro/IE_Event_Bot/commit/a5cc0ad523b3f82186a7a1f9241e6ba55fbc8bb5))
* **e2e:** 所有イベントを通常ポーリングへ接続 ([9d54ccf](https://github.com/ichipiro/IE_Event_Bot/commit/9d54ccf3cd758e75c03c09b3e2a9ca2eb0afce6a))
* **e2e:** 終日と繰返しを含む全件同期の検証を追加 ([1ca952b](https://github.com/ichipiro/IE_Event_Bot/commit/1ca952b9a5297e4dcbe5de837ac0669d3af66a20))
* **e2e:** 複数イベントのKV残件を別HTTPで消化 ([9b02fbd](https://github.com/ichipiro/IE_Event_Bot/commit/9b02fbd5a93d249379fe9009d08e73316cee0734))
* **e2e:** 複数イベントのKV残件を別HTTPで消化 ([87460d2](https://github.com/ichipiro/IE_Event_Bot/commit/87460d282d399503636f42722164f5a0645b76f3))
* **e2e:** 通常google同期の実サービス検証を追加 ([26a86e6](https://github.com/ichipiro/IE_Event_Bot/commit/26a86e6f293e5c7fc6f1a9687abaf758455d6c7a))
* **e2e:** 通常google同期の所有検証と回収を追加 ([ddbc8c8](https://github.com/ichipiro/IE_Event_Bot/commit/ddbc8c8f112819c4b3234ba01eeedf705c3d934e))
* **e2e:** 通常HTTPと共有KVで全体同期を検証 ([bd0bbbb](https://github.com/ichipiro/IE_Event_Bot/commit/bd0bbbbcf02767f5537aec13cd82760af2b016c3))
* **e2e:** 通常KVと外部fixtureの所有状態を検証 ([286fba4](https://github.com/ichipiro/IE_Event_Bot/commit/286fba4801e9150ee6b502b72fd0cfa5e87485d7))
* **e2e:** 通常KVの手動検証workflowを追加 ([1e073cd](https://github.com/ichipiro/IE_Event_Bot/commit/1e073cdc9e818fb7089417be96da3c0ba9a12599))
* **e2e:** 通常KVの隔離と所有状態の検証を追加 ([438ecdc](https://github.com/ichipiro/IE_Event_Bot/commit/438ecdcc1e70173892da0827162306095fa61748))
* **e2e:** 通常KVの隔離と所有状態の検証を追加 ([e714623](https://github.com/ichipiro/IE_Event_Bot/commit/e714623001d9f3f1eaa0717911ff8e8ce81e6632))
* **e2e:** 通常notion cleanupと共有kvの検証を追加 ([792dd78](https://github.com/ichipiro/IE_Event_Bot/commit/792dd783bd5d65a5f064fcdb06f6ec3ce7ae837e))
* **e2e:** 通常watchと共有Webhook同期を検証 ([b2c1f6b](https://github.com/ichipiro/IE_Event_Bot/commit/b2c1f6b4fbfe903372426f7902afc0d5280134f9))
* **e2e:** 通常ポーリングのGoogle反映を検証 ([2abc353](https://github.com/ichipiro/IE_Event_Bot/commit/2abc35331ce50051f69ddca3947c94a52cea945e))
* **e2e:** 通常ポーリングのGoogle反映を検証 ([2e58891](https://github.com/ichipiro/IE_Event_Bot/commit/2e58891b10dbc015d6bc66bb610fa836b8283e5f))
* **sync:** 再試行処理と全E2E完了記録を統合 ([1a3eaa1](https://github.com/ichipiro/IE_Event_Bot/commit/1a3eaa1ce585444a72e02e570059873ddec1ad58))


### Bug Fixes

* **ci:** 件名検査を日本語の開発規則へ合わせる ([04ed603](https://github.com/ichipiro/IE_Event_Bot/commit/04ed603c9330189ae5a010613bc1d11e5e0ba90e))
* **e2e:** api拒否試験の所有資源を回収可能にする ([834930f](https://github.com/ichipiro/IE_Event_Bot/commit/834930fd2a9d4a81105cf280a2813ba55aca1506))
* **e2e:** Cron予定時刻の分境界制約を除去 ([4b39ecf](https://github.com/ichipiro/IE_Event_Bot/commit/4b39ecfc7fa593ef39582a8d418e34e01c2dceec))
* **e2e:** Cron検証期限の時計読取りを統一 ([4d25457](https://github.com/ichipiro/IE_Event_Bot/commit/4d25457281d08e01e2149f1c3f675ba5ef93afdc))
* **e2e:** Cron診断の時刻をミリ秒で送信 ([8e5a7d8](https://github.com/ichipiro/IE_Event_Bot/commit/8e5a7d86e019795238c0fdc59871a5a7e7e2cccf))
* **e2e:** deploy完了前のMCPタイムアウトを防ぐ ([f656e84](https://github.com/ichipiro/IE_Event_Bot/commit/f656e845cda7e7bf14bc9b32d3af876eca4d412e))
* **e2e:** Google制御ロックの解放失敗箇所を記録 ([538008a](https://github.com/ichipiro/IE_Event_Bot/commit/538008a2cb40675e81e8476ca9ee7316b8e285cd))
* **e2e:** google同期のKV欠損値を正規化 ([24c9e53](https://github.com/ichipiro/IE_Event_Bot/commit/24c9e53609bfd5a4fc3d832ab3bbd64f8b91ddca))
* **e2e:** Google同期の書込み前version拒否を再試行 ([16b54d5](https://github.com/ichipiro/IE_Event_Bot/commit/16b54d5a1c13a7dbb1536119c53128fa221421a3))
* **e2e:** inspect all telemetry message fields ([4bdcd1a](https://github.com/ichipiro/IE_Event_Bot/commit/4bdcd1a7a5d56c5a7f10849370aeff82d2788cfb))
* **e2e:** Notion照会試験の削除履歴を小さく保持 ([fb97396](https://github.com/ichipiro/IE_Event_Bot/commit/fb97396d4fd34f4870b96980d7cab7e1cc53d3c1))
* **e2e:** Webhook処理を継続し中断資源を回収する ([1afdf4f](https://github.com/ichipiro/IE_Event_Bot/commit/1afdf4f3f0195d840c3e2cdfafa9307c72c9e877))
* **e2e:** ログ照会403の診断情報を補う ([6a2674b](https://github.com/ichipiro/IE_Event_Bot/commit/6a2674b4575d3425600fa661ae07484cea8999de))
* **e2e:** 件数境界の検証応答をMCP契約へ合わせる ([f353292](https://github.com/ichipiro/IE_Event_Bot/commit/f35329288d23829147ab668029e662238fdb1a42))
* **e2e:** 全件同期と呼出し側の待機時間を揃える ([0c6a382](https://github.com/ichipiro/IE_Event_Bot/commit/0c6a3825277cb95e2e2cdac307a23c2e6f5afec0))
* **e2e:** 再deployの所有シナリオ判定を修正 ([3655d3d](https://github.com/ichipiro/IE_Event_Bot/commit/3655d3d79d45e7ce272cd333b389f4bcd5205bd4))
* **e2e:** 再検証の失敗を前回成功で上書きしない ([5ee3815](https://github.com/ichipiro/IE_Event_Bot/commit/5ee381568c51a5053b721d6a6d2e24d630047557))
* **e2e:** 処理後のロック解放失敗から所有資源を回収 ([b7a9786](https://github.com/ichipiro/IE_Event_Bot/commit/b7a978677bfdbc661cff53e26c93e46547dd5c05))
* **e2e:** 同期後の状態読取りの通信失敗を再試行 ([fb28b94](https://github.com/ichipiro/IE_Event_Bot/commit/fb28b94c938f1e0a7baaf2216394059b6d9d38f1))
* **e2e:** 既存の削除履歴を保護してcalendarを再利用 ([be79058](https://github.com/ichipiro/IE_Event_Bot/commit/be790581aaff3136f0fe032556d091541e013186))
* **e2e:** 既知の削除履歴の対応表を許容 ([02bc807](https://github.com/ichipiro/IE_Event_Bot/commit/02bc807275c03cf9dddfa3aa7367dd5ae91100cb))
* **e2e:** 旧通知の検証を別HTTP段階へ分割 ([07143c2](https://github.com/ichipiro/IE_Event_Bot/commit/07143c26454d5b144cda58c40c14b9520a70e1b6))
* **e2e:** 期限切れロックと旧revisionによる回収停止を解消 ([df98874](https://github.com/ichipiro/IE_Event_Bot/commit/df988747d1ba4a1376362b6e3308acf4fb229e36))
* **e2e:** 検証用contextの型エラーを解消 ([078584b](https://github.com/ichipiro/IE_Event_Bot/commit/078584b3ce87d76b1eded576ed83f498d3c7cf7a))
* **e2e:** 状態障害検証をケースごとの要求へ分割 ([68ebbf2](https://github.com/ichipiro/IE_Event_Bot/commit/68ebbf21471106f7e065eecc41504245386aec31))
* **e2e:** 状態障害検証をケースごとの要求へ分割 ([c1740e2](https://github.com/ichipiro/IE_Event_Bot/commit/c1740e2f0a5d11dedefe4c06df24f318110ef1f2))
* **e2e:** 確認通信の診断と上限付き再試行を追加 ([e8e7e9d](https://github.com/ichipiro/IE_Event_Bot/commit/e8e7e9d3208fc010e7089281359c2c37126dc981))
* **e2e:** 統合時に重複したimportを除去 ([e3c71dc](https://github.com/ichipiro/IE_Event_Bot/commit/e3c71dcec037b514a452fd84bf83961fd25e7bac))
* **e2e:** 認証へKV無効の状態を渡す ([5d4a12b](https://github.com/ichipiro/IE_Event_Bot/commit/5d4a12bc40950b3e5a844db3355d04602edf119e))
* **jobs:** KV保存失敗時に同じ値だけ再試行する ([d283b8c](https://github.com/ichipiro/IE_Event_Bot/commit/d283b8c012f37fee193ba39876e725ccf3e14ddb))
* **jobs:** Notion一覧失敗時の状態保持と再試行を検証 ([9f53e3b](https://github.com/ichipiro/IE_Event_Bot/commit/9f53e3b3b72c03bb4a731a915ce8621d6981f7a9))
* **jobs:** 通常ジョブの失敗後再試行を検証 ([cbfa2d9](https://github.com/ichipiro/IE_Event_Bot/commit/cbfa2d9eb8050cf1026a22074a79e323e93d7c1b))
* **reminder:** 一覧のレート制限を待機して再試行 ([cc97b8b](https://github.com/ichipiro/IE_Event_Bot/commit/cc97b8bc2f20f6d7a52d3e9c4e0a412368479aec))
* **reminder:** 一覧取得失敗を成功扱いしない ([cffa9c2](https://github.com/ichipiro/IE_Event_Bot/commit/cffa9c2e00615f65b0a3f0d36949b7524909ac5d))
* **sync:** googleからdiscordへの反映失敗を次回へ繰り越す ([07263f2](https://github.com/ichipiro/IE_Event_Bot/commit/07263f2de5902cbaf5b701d48dc99b06617751b8))
* **sync:** googleからdiscordへの反映失敗を次回へ繰り越す ([7336d0d](https://github.com/ichipiro/IE_Event_Bot/commit/7336d0d96161f79d3b4b45df456f572d022c22c3))
* **sync:** google同期のHTTP失敗を再試行する ([33f5e48](https://github.com/ichipiro/IE_Event_Bot/commit/33f5e487efa99a56b93fd9285523712dc9f45732))
* **sync:** recover failed lock releases ([ef6bba7](https://github.com/ichipiro/IE_Event_Bot/commit/ef6bba7daf95590f30c1fb272076174f5e640029))
* **sync:** ロック解放失敗を可視化して自動復旧する ([c360b10](https://github.com/ichipiro/IE_Event_Bot/commit/c360b1030e5880a63458edc078397cbc68c11422))
* **sync:** ロック解放失敗を可視化して自動復旧する ([5637ca9](https://github.com/ichipiro/IE_Event_Bot/commit/5637ca997357609131045a6ea34a934f58fcbb02))
* **sync:** 作成通知の再試行を保持しE2Eを追加 ([24571b6](https://github.com/ichipiro/IE_Event_Bot/commit/24571b655d8a5add793724b8fc9a9f0b8336bec8))
* **sync:** 作成通知の繰越と失敗再試行を保持 ([6da53c9](https://github.com/ichipiro/IE_Event_Bot/commit/6da53c9700fa921a4e1b9fee71fe091098557bd8))
* **sync:** 再試行キューの保存順と単独同期の排他を修正 ([b920d98](https://github.com/ichipiro/IE_Event_Bot/commit/b920d9812df4702ef673a2cba830853e1a18689e))
* **sync:** 再試行キューの保存順と単独同期の排他を修正 ([5d0b577](https://github.com/ichipiro/IE_Event_Bot/commit/5d0b577312679efed1f82b4c6393256cbc48ba46))
* **sync:** 古いqueueの残件回復と失効した同期の停止 ([395a53b](https://github.com/ichipiro/IE_Event_Bot/commit/395a53bcab66cf0408d42c107b6b87e6f31443d3))
* **sync:** 古いqueueの残件回復と失効した同期の停止 ([b21cb8f](https://github.com/ichipiro/IE_Event_Bot/commit/b21cb8fcab967916e9938727fc7ff63a9aef9fb7))
* **webhook:** 通知を永続化して失敗後の同期を再試行 ([7ce438e](https://github.com/ichipiro/IE_Event_Bot/commit/7ce438ec2cb71f0016da8ac3bdfcea109e7f6690))


### Documentation

* **e2e:** 28段階の実サービス検証と回収成功を記録 ([65f68dd](https://github.com/ichipiro/IE_Event_Bot/commit/65f68dde500da6e72976a0053c9441ff6dadab45))
* **e2e:** calendar削除履歴の診断結果を記録 ([c6f162a](https://github.com/ichipiro/IE_Event_Bot/commit/c6f162a6f92a0072708ccdab2b4ad4809bdd4615))
* **e2e:** Google matrixの残存資源回収を記録 ([f09ba29](https://github.com/ichipiro/IE_Event_Bot/commit/f09ba2933196e274c0214b4b9d0628316255f44e))
* **e2e:** Google matrix全18段階の成功を記録 ([439cad7](https://github.com/ichipiro/IE_Event_Bot/commit/439cad7b97fc3638cd454e0781d102a4b98c891b))
* **e2e:** Google件数境界の成功と全件回収を記録 ([e0ac814](https://github.com/ichipiro/IE_Event_Bot/commit/e0ac8141c87ff2a7e43469653c3f907d8fe608f7))
* **e2e:** Google全件適用と共有KV回収の成功を記録 ([8fe8830](https://github.com/ichipiro/IE_Event_Bot/commit/8fe8830a9e13c0cb17c5a9367b8ac115dab01a50))
* **e2e:** google同期のapi拒否と回復の成功を記録 ([e7e1f85](https://github.com/ichipiro/IE_Event_Bot/commit/e7e1f85e6cc66069e9a242d8339edba46751b007))
* **e2e:** google同期の所有資源回収を記録 ([d184a2a](https://github.com/ichipiro/IE_Event_Bot/commit/d184a2aac578b9f20e05ba0bc960726574880285))
* **e2e:** google同期の部分失敗と回復の検証結果を記録 ([ded6782](https://github.com/ichipiro/IE_Event_Bot/commit/ded678269e65d22e79a45ab0707fb62e7fd9e024))
* **e2e:** Notion一覧失敗の再試行成功を記録 ([e346afd](https://github.com/ichipiro/IE_Event_Bot/commit/e346afdf3d3f5d87afed4b07aff757903e52d41a))
* **e2e:** Notion作成復旧の成功と回収を記録 ([dc32fc9](https://github.com/ichipiro/IE_Event_Bot/commit/dc32fc92c5775c266f4c7e7852474d43ba92b1f4))
* **e2e:** Notion書戻し復旧の成功を記録 ([58f75f1](https://github.com/ichipiro/IE_Event_Bot/commit/58f75f1396fa87604ffa1f91c90611ee1a8b13dd))
* **e2e:** Notion照会復旧の成功と回収を記録 ([9838432](https://github.com/ichipiro/IE_Event_Bot/commit/9838432629d037dbed03fe9a7728f2206bb07095))
* **e2e:** record lock recovery verification ([21c9f95](https://github.com/ichipiro/IE_Event_Bot/commit/21c9f958ab47f7c6fbb209dff3e03d91fced1bc6))
* **e2e:** Telemetry権限の誤案内を訂正 ([4c448eb](https://github.com/ichipiro/IE_Event_Bot/commit/4c448eb59abfef3e99acb41d30690af7e1a044ea))
* **e2e:** watchと共有Webhookの実行結果を記録 ([6eb597a](https://github.com/ichipiro/IE_Event_Bot/commit/6eb597a5050ce5992d429d59da7bfc45714fa34e))
* **e2e:** Webhook同期修正後の成功証跡を記録 ([fe835fa](https://github.com/ichipiro/IE_Event_Bot/commit/fe835fa23a35e116784f68c96292a64fee6b662a))
* **e2e:** ログ照会成功と原因調査の限界を記録 ([84b685a](https://github.com/ichipiro/IE_Event_Bot/commit/84b685a374bef70e5c35d2e49821e4aee173105c))
* **e2e:** ログ診断APIの403と未解決範囲を記録 ([b7b0bef](https://github.com/ichipiro/IE_Event_Bot/commit/b7b0beff44e6a2d00a2db10dde867436b95d07bd))
* **e2e:** ロック競合の実環境検証結果を記録 ([7a68f17](https://github.com/ichipiro/IE_Event_Bot/commit/7a68f17dd401f35129c6935d1aa95d50d0ca91b2))
* **e2e:** ロック競合の実環境検証結果を記録 ([9a127cc](https://github.com/ichipiro/IE_Event_Bot/commit/9a127cc36feb8d9c8f8863d006841a28d1090948))
* **e2e:** 三サービスの実動検証を記録 ([c62e561](https://github.com/ichipiro/IE_Event_Bot/commit/c62e561f9b1ccda6f57bcd8a1699a5286177ced7))
* **e2e:** 予定形式と共有queue再試行の成功を記録 ([022f196](https://github.com/ichipiro/IE_Event_Bot/commit/022f196aff104974cfc6a0551decd9bd2bb63e3d))
* **e2e:** 全件検証の共有KV開始条件拒否を記録 ([47ee8c7](https://github.com/ichipiro/IE_Event_Bot/commit/47ee8c769dba40d1ae0e39c60a6f5916749619e9))
* **e2e:** 全体同期9段階の検証と回収成功を記録 ([4b0dceb](https://github.com/ichipiro/IE_Event_Bot/commit/4b0dceb3e02d9e4cee78df05de4df29519c871bf))
* **e2e:** 再デプロイ継続の実サービス証拠を記録 ([bb498b6](https://github.com/ichipiro/IE_Event_Bot/commit/bb498b63511041a223d9c81810ed0061962b899b))
* **e2e:** 分割した状態障害検証の成功を記録 ([2df3d80](https://github.com/ichipiro/IE_Event_Bot/commit/2df3d80b21e8bc1c9af6e135e60995c14e41f139))
* **e2e:** 同時resumeの実サービス証拠を記録 ([fece433](https://github.com/ichipiro/IE_Event_Bot/commit/fece433d33b95e8af1b5fd53ebbcd0bb631e1d85))
* **e2e:** 回収workflowの承認待ちを記録 ([5055c46](https://github.com/ichipiro/IE_Event_Bot/commit/5055c466a454762ea04f0732f9e570918f2c9a10))
* **e2e:** 外部fixtureと通常KVの実証を記録 ([8325a38](https://github.com/ichipiro/IE_Event_Bot/commit/8325a38467c3e662943128ecec3097f3604d2076))
* **e2e:** 実Cron競合の成功と回収を記録 ([c8aa98e](https://github.com/ichipiro/IE_Event_Bot/commit/c8aa98ec6e6372d2d379ed39dbf7b2445cd83ae9))
* **e2e:** 実Cron起動と回収の成功証跡を記録 ([7e2b7f3](https://github.com/ichipiro/IE_Event_Bot/commit/7e2b7f3aa04a328bb530d318c66b70270b49a771))
* **e2e:** 対象内試験の完了と回収証跡を確定 ([c71a7b8](https://github.com/ichipiro/IE_Event_Bot/commit/c71a7b890b0ee9f2db3560c215bc240011e9fdb3))
* **e2e:** 応答本文破棄の実サービス証拠を記録 ([f371013](https://github.com/ichipiro/IE_Event_Bot/commit/f371013b6589c2731037fb48185bc37e51a9788d))
* **e2e:** 新規KVでのCalendar開始条件拒否を記録 ([178e2cc](https://github.com/ichipiro/IE_Event_Bot/commit/178e2cc4b45d17c6ec09c9d76e3b34d82a753ffa))
* **e2e:** 有効account tokenでのログ照会拒否を記録 ([a8cc5eb](https://github.com/ichipiro/IE_Event_Bot/commit/a8cc5ebfd12366e94a23903984b2d3fb8e5f1a45))
* **e2e:** 棚卸しと旧通知の実環境検証を記録 ([88426f4](https://github.com/ichipiro/IE_Event_Bot/commit/88426f46d80cf320a97deaf09bcbf7b17e9dcdf9))
* **e2e:** 権限変更対象と使用中tokenの相違を記録 ([874aa44](https://github.com/ichipiro/IE_Event_Bot/commit/874aa44e82ab9685bbfa1da8a9a6c703c9477368))
* **e2e:** 複数イベントの実サービス検証を記録 ([a087a15](https://github.com/ichipiro/IE_Event_Bot/commit/a087a156009aaab32981bef59178f6c2d4972b6c))
* **e2e:** 解放診断版の実環境検証と回収を記録 ([2c63ec1](https://github.com/ichipiro/IE_Event_Bot/commit/2c63ec1e885c218ea14b6b91f5c75499f6a1efdd))
* **e2e:** 通常google同期の実サービス検証結果を記録 ([1e70982](https://github.com/ichipiro/IE_Event_Bot/commit/1e7098223409173ffd2432274cf7ad0bf9eedfbb))
* **e2e:** 通常HTTP全体同期の成功と回収を記録 ([327e09d](https://github.com/ichipiro/IE_Event_Bot/commit/327e09dafe812b2d05cc1122da70c91ed741cd0d))
* **e2e:** 通常KVとDOの実環境検証を記録 ([55bb819](https://github.com/ichipiro/IE_Event_Bot/commit/55bb81999ea7966969f4a4a30e74897160338dcc))
* **e2e:** 通常notion cleanupの成功を記録 ([01cac73](https://github.com/ichipiro/IE_Event_Bot/commit/01cac739fb600167553cd4db7900e1a611815610))
* **e2e:** 通常Q&Aの成功証跡を記録 ([732d70c](https://github.com/ichipiro/IE_Event_Bot/commit/732d70c0b753af445bfff0fb8c1f26caf2127dde))
* **e2e:** 通常ジョブKV再試行の実行結果を記録 ([b253403](https://github.com/ichipiro/IE_Event_Bot/commit/b2534037167b11c394aaed9070c780e575d47825))
* **e2e:** 通常ジョブ再試行の成功証跡を記録 ([3bb2ec0](https://github.com/ichipiro/IE_Event_Bot/commit/3bb2ec0f1aefa611dc5318597ca1b1ed23a2b9b3))
* **e2e:** 通常ポーリングの実サービス検証を記録 ([628070e](https://github.com/ichipiro/IE_Event_Bot/commit/628070e16451c376223547db24d970405be23cbd))
* **e2e:** 通常リマインドの成功証跡を記録 ([68d4198](https://github.com/ichipiro/IE_Event_Bot/commit/68d419892c9cbd56312e9105580376963912e12d))
* **e2e:** 通知再試行の実サービス検証結果を記録 ([cec18a9](https://github.com/ichipiro/IE_Event_Bot/commit/cec18a96ada8013fbfaa67a8c42d7dcf96cf4327))
* **e2e:** 通知再試行の実サービス検証結果を記録 ([cfe745e](https://github.com/ichipiro/IE_Event_Bot/commit/cfe745e7aee4951689fd6206d8c2308776240b77))
* 統合後の回帰検証を記録 ([f35509c](https://github.com/ichipiro/IE_Event_Bot/commit/f35509c363f2c72e649cc7872f46c65d9752c0a2))

## [0.5.1](https://github.com/ichipiro/IE_Event_Bot/compare/v0.5.0...v0.5.1) (2026-09-11)


### Documentation

* **e2e:** record verified Discord replay run ([f83020b](https://github.com/ichipiro/IE_Event_Bot/commit/f83020bf0f33b6c56146629498b9c7220b3d6ec2))

## [0.5.0](https://github.com/ichipiro/IE_Event_Bot/compare/v0.4.0...v0.5.0) (2026-09-11)


### Features

* **e2e:** resume Discord delta after verified update ([22f26ba](https://github.com/ichipiro/IE_Event_Bot/commit/22f26bacbda7a32ba2d9fc75346dbc3d649963b3))
* **e2e:** resume Discord delta after verified update ([2d8b73c](https://github.com/ichipiro/IE_Event_Bot/commit/2d8b73cc35ac41243a867108d01bc77c5a48821c))


### Documentation

* **e2e:** record verified Discord update checkpoint run ([fec9cd3](https://github.com/ichipiro/IE_Event_Bot/commit/fec9cd30fa315546b2ee48ca2973938e6090c047))

## [0.4.0](https://github.com/ichipiro/IE_Event_Bot/compare/v0.3.1...v0.4.0) (2026-09-11)


### Features

* **e2e:** add Discord to Google scenario ([67805c1](https://github.com/ichipiro/IE_Event_Bot/commit/67805c1121faa9c114449cae361c425138299f20))
* **e2e:** add Discord to Google scenario ([676fd9a](https://github.com/ichipiro/IE_Event_Bot/commit/676fd9a3cf2b38197ec9799157471415bf857a23))
* **e2e:** add Discord to Notion scenario ([30494fd](https://github.com/ichipiro/IE_Event_Bot/commit/30494fd68ca4715f689c8c4b7fe02d00bf48523d))
* **e2e:** add Discord to Notion scenario ([5dd15b4](https://github.com/ichipiro/IE_Event_Bot/commit/5dd15b4564b2cd1118ff64b0bc67db5ad4fb014d))
* **e2e:** add Google to Discord scenario ([f6eaf14](https://github.com/ichipiro/IE_Event_Bot/commit/f6eaf1473475c1a405c9e79de2902e411c2dd02a))
* **e2e:** add Google to Discord scenario ([50947a5](https://github.com/ichipiro/IE_Event_Bot/commit/50947a53dab87427fba4154aaeae8b544f40d6c3))
* **e2e:** add Google to Notion scenario ([3fc6cb6](https://github.com/ichipiro/IE_Event_Bot/commit/3fc6cb69adb81d41f4c2422dc50465573306a945))
* **e2e:** add Google to Notion scenario ([e2608db](https://github.com/ichipiro/IE_Event_Bot/commit/e2608db36c048a46e1c73929ed0bf12743491aa9))
* **e2e:** add Notion cleanup scenario ([a94bc64](https://github.com/ichipiro/IE_Event_Bot/commit/a94bc64054b9019509c9ea9d96a0a75b18189e3a))
* **e2e:** add Notion cleanup scenario ([ef35e6e](https://github.com/ichipiro/IE_Event_Bot/commit/ef35e6ef911f467e0363fe67932aa6986f5fcc69))
* **e2e:** add QA notification scenario ([51fa2f2](https://github.com/ichipiro/IE_Event_Bot/commit/51fa2f2cd1075f3d34581580a8dbfdb80d41a660))
* **e2e:** add QA notification scenario ([aad4536](https://github.com/ichipiro/IE_Event_Bot/commit/aad453609adfd924f7f6c87e7bec29a60e6546c7))
* **e2e:** add reminder notification scenario ([85c9dab](https://github.com/ichipiro/IE_Event_Bot/commit/85c9dab875fa66fd79526d8af6da0b7f0efc3054))
* **e2e:** add reminder notification scenario ([db6296b](https://github.com/ichipiro/IE_Event_Bot/commit/db6296bd77ef21fad0a9b62c220005e0f299c4d8))
* **e2e:** add resumable Discord delta sync scenario ([3210352](https://github.com/ichipiro/IE_Event_Bot/commit/3210352d55a46f874998b6cd1dc2775b381a98ec))
* **e2e:** add webhook dispatch simulation ([6c2370c](https://github.com/ichipiro/IE_Event_Bot/commit/6c2370c93dc6a8aec48697ab28b17d6c7656ab5e))
* **e2e:** add webhook dispatch simulation ([667e54e](https://github.com/ichipiro/IE_Event_Bot/commit/667e54e0baf4f88d0995759d3d25c5993e0b08f6))
* **e2e:** verify Google change webhook dispatch ([38e3307](https://github.com/ichipiro/IE_Event_Bot/commit/38e330717fb83e0cb7299e7fc4d35c9fb060bcba))
* **e2e:** verify Google change webhook dispatch ([c167fc5](https://github.com/ichipiro/IE_Event_Bot/commit/c167fc5f0cc243a1ce91e71f68e771c39523da59))
* **e2e:** verify Google webhook delivery ([d9ec80f](https://github.com/ichipiro/IE_Event_Bot/commit/d9ec80fe5abcf8e5087b45f4a14fc4b7ecde8ec9))
* **e2e:** verify Google webhook delivery ([a06a2f2](https://github.com/ichipiro/IE_Event_Bot/commit/a06a2f2827e0c2ea5cdf683d644c090b02f5159e))
* **e2e:** verify Google webhook delivery ([#44](https://github.com/ichipiro/IE_Event_Bot/issues/44)) ([d9ec80f](https://github.com/ichipiro/IE_Event_Bot/commit/d9ec80fe5abcf8e5087b45f4a14fc4b7ecde8ec9))
* **e2e:** verify resumable Discord delta sync ([a9922e0](https://github.com/ichipiro/IE_Event_Bot/commit/a9922e07de4726ee131dd9aba422f171a5d36437))
* **e2e:** verify webhook ingress dedupe ([59ba555](https://github.com/ichipiro/IE_Event_Bot/commit/59ba555fa9c559782f7c9b8c7e6b20ab5b1e34c4))
* **e2e:** verify webhook ingress dedupe ([fddf9db](https://github.com/ichipiro/IE_Event_Bot/commit/fddf9db87b5305d9c1b5c167565d0f5fee8433d7))


### Bug Fixes

* **e2e:** block unowned orchestration routes ([70bfc5e](https://github.com/ichipiro/IE_Event_Bot/commit/70bfc5eb5100ded70cb78f5a64cd72e491bbc977))
* **e2e:** block unowned orchestration routes ([dac573d](https://github.com/ichipiro/IE_Event_Bot/commit/dac573deeb9d3f009e9c87b70dd25c0ef337f10a))
* **e2e:** compare Notion timestamps semantically ([36b6281](https://github.com/ichipiro/IE_Event_Bot/commit/36b6281033a0a413b38e01fe187ace046df6e76a))
* **e2e:** compare Notion timestamps semantically ([e6fa117](https://github.com/ichipiro/IE_Event_Bot/commit/e6fa1179ca104b3c0ce78cc67249b5bf6d7c9c23))
* **e2e:** mask worker origin and preserve diagnostics ([d021f3a](https://github.com/ichipiro/IE_Event_Bot/commit/d021f3ad2965a455d70cd822eb2e587f58cdb352))
* **e2e:** mask worker origin and preserve diagnostics ([6efc7c8](https://github.com/ichipiro/IE_Event_Bot/commit/6efc7c8fe6ab06a0bb9990e361cf38eb4dcaa4fd))
* **e2e:** normalize Notion fixture precision ([f839326](https://github.com/ichipiro/IE_Event_Bot/commit/f839326211f953d5d00b1f32a2e2433cb47ad641))
* **e2e:** normalize Notion fixture precision ([bb5201e](https://github.com/ichipiro/IE_Event_Bot/commit/bb5201e93369ee6696ab23d3e61ef9ac167d2d75))
* **e2e:** recover Discord delta failures before Notion apply ([65c226f](https://github.com/ichipiro/IE_Event_Bot/commit/65c226f065f7699942120a4e7736a5cb4ff8bef9))
* **e2e:** retry Discord list throttling and gate write revisions ([7c10059](https://github.com/ichipiro/IE_Event_Bot/commit/7c1005958c003592f040332da6f905ee4509c0c4))
* **e2e:** verify deployed worker revision ([861655b](https://github.com/ichipiro/IE_Event_Bot/commit/861655b89b2e31e5569d89dd2a948a7b81a3acad))
* **e2e:** verify deployed worker revision ([b7da11d](https://github.com/ichipiro/IE_Event_Bot/commit/b7da11df05488b337ae9c97a173bf5aa753dcc02))


### Documentation

* avoid links to local-only files ([adcf26d](https://github.com/ichipiro/IE_Event_Bot/commit/adcf26d5073908f783133d3bcfd56ce9b32756c1))
* avoid links to local-only files ([fd7e24d](https://github.com/ichipiro/IE_Event_Bot/commit/fd7e24d8a76905777219063fdc6eb480f2758910))
* **e2e:** record Discord to Google run ([b6e1b39](https://github.com/ichipiro/IE_Event_Bot/commit/b6e1b3912daedccbc045e960b2fc7da936c76140))
* **e2e:** record Discord to Google run ([189aab4](https://github.com/ichipiro/IE_Event_Bot/commit/189aab4a4fbe7ed2ab3a4c544a7792b0d91c6282))
* **e2e:** record Discord to Notion run ([f9c8db3](https://github.com/ichipiro/IE_Event_Bot/commit/f9c8db354c8dab54d34d66a8dc4a9eab4534b73e))
* **e2e:** record Discord to Notion run ([c0adfd3](https://github.com/ichipiro/IE_Event_Bot/commit/c0adfd32e5c2d2e09026d32db710f9f1246d9413))
* **e2e:** record Google to Discord run ([02fcca3](https://github.com/ichipiro/IE_Event_Bot/commit/02fcca3b692ebf37f372c6d01ebc117d38fdf6a6))
* **e2e:** record Google to Discord run ([f40dc3e](https://github.com/ichipiro/IE_Event_Bot/commit/f40dc3e95ba581478e6a105e61c48bf114afe15b))
* **e2e:** record Google to Notion run ([3ffbf3d](https://github.com/ichipiro/IE_Event_Bot/commit/3ffbf3d3d06281fc5aca6cba9aa3328ae5bbe7e3))
* **e2e:** record Google to Notion run ([b19ec86](https://github.com/ichipiro/IE_Event_Bot/commit/b19ec869c064af9038669ec2e484147738d79413))
* **e2e:** record Google webhook change run ([e53d772](https://github.com/ichipiro/IE_Event_Bot/commit/e53d7724199af652ded20f1d56a9d8361b3b0b5a))
* **e2e:** record Google webhook change run ([7cdd616](https://github.com/ichipiro/IE_Event_Bot/commit/7cdd616ad39532fc336dd6bc05736dda71ae963f))
* **e2e:** record Google webhook delivery run ([38e4c07](https://github.com/ichipiro/IE_Event_Bot/commit/38e4c0786c72d92e0a251e3f28a17cf91d559ea3))
* **e2e:** record Google webhook delivery run ([57574b1](https://github.com/ichipiro/IE_Event_Bot/commit/57574b12a58fb665917da0a7df28a56b5d42877e))
* **e2e:** record Notion cleanup run ([bb43e47](https://github.com/ichipiro/IE_Event_Bot/commit/bb43e47c88b7d50d0dcd6f020e07a24bd88fe8e8))
* **e2e:** record Notion cleanup run ([42917c3](https://github.com/ichipiro/IE_Event_Bot/commit/42917c31eb9b0e69e2ce2df563a2ddf91faff3cf))
* **e2e:** record QA notification run ([a2428f7](https://github.com/ichipiro/IE_Event_Bot/commit/a2428f7e55015c99976c3311111c591a4c633a89))
* **e2e:** record QA notification run ([6794716](https://github.com/ichipiro/IE_Event_Bot/commit/679471634603ac8232671a5d49468c372e02fb1f))
* **e2e:** record reminder notification run ([bc76a25](https://github.com/ichipiro/IE_Event_Bot/commit/bc76a25087a585b424da1d6ed4ed0063917f1ae3))
* **e2e:** record reminder notification run ([bcf82d2](https://github.com/ichipiro/IE_Event_Bot/commit/bcf82d27411edf871d741ec7b0850dcc91ed990d))
* **e2e:** record verified Discord delta run and recovery ([de86f6f](https://github.com/ichipiro/IE_Event_Bot/commit/de86f6f23be8deb47401d2ce912e97334b01b32e))
* **e2e:** record webhook ingress run ([189a2de](https://github.com/ichipiro/IE_Event_Bot/commit/189a2dec7cc20fc22bd2490860edff7549b7bcb0))
* **e2e:** record webhook ingress run ([e89fc54](https://github.com/ichipiro/IE_Event_Bot/commit/e89fc54bef6f6e1a137c34aa5d8ea2437213a4c1))
* **e2e:** record webhook simulation run ([48bb33a](https://github.com/ichipiro/IE_Event_Bot/commit/48bb33a1ffb99660c1d84af4cbb4327695f89715))
* **e2e:** record webhook simulation run ([eeed5cb](https://github.com/ichipiro/IE_Event_Bot/commit/eeed5cba8434cea4e6e91886936f8be26938181a))

## [0.3.1](https://github.com/ichipiro/IE_Event_Bot/compare/v0.3.0...v0.3.1) (2026-09-01)


### Documentation

* **architecture:** refresh E2E coverage and test guidance ([4903d5c](https://github.com/ichipiro/IE_Event_Bot/commit/4903d5ce3c91217a94581e8b896efa21424c958f))

## [0.3.0](https://github.com/ichipiro/IE_Event_Bot/compare/v0.2.0...v0.3.0) (2026-09-01)


### Features

* **e2e:** add isolated test workflow ([694b0b2](https://github.com/ichipiro/IE_Event_Bot/commit/694b0b26217d5c2d12adfc7bc44a3d8c3f33065f))
* **e2e:** add isolated test workflow ([#12](https://github.com/ichipiro/IE_Event_Bot/issues/12)) ([dcd8780](https://github.com/ichipiro/IE_Event_Bot/commit/dcd8780aa35db34473a3fbd8f1ca83dd5b17ebac))

## [0.2.0](https://github.com/ichipiro/IE_Event_Bot/releases/tag/v0.2.0) (2026-08-29)


### Features

* release ([#1](https://github.com/ichipiro/IE_Event_Bot/issues/1)) ([780866e](https://github.com/ichipiro/IE_Event_Bot/commit/780866e24c902d22052b7631d388fbdee5268b93))
* release v2 ([#3](https://github.com/ichipiro/IE_Event_Bot/issues/3)) ([84d050d](https://github.com/ichipiro/IE_Event_Bot/commit/84d050d12deab1df9362ad64d60c595e7202b633))
* release v2 ([#3](https://github.com/ichipiro/IE_Event_Bot/issues/3)) ([#4](https://github.com/ichipiro/IE_Event_Bot/issues/4)) ([8777242](https://github.com/ichipiro/IE_Event_Bot/commit/8777242248dcf7fcfb78c8fb8c1be031113ef4d6))
* release v2 ([#3](https://github.com/ichipiro/IE_Event_Bot/issues/3)) ([#4](https://github.com/ichipiro/IE_Event_Bot/issues/4)) ([#5](https://github.com/ichipiro/IE_Event_Bot/issues/5)) ([57ed4c4](https://github.com/ichipiro/IE_Event_Bot/commit/57ed4c407cebcb03eb87c124b905dc74be2387c9))


### Bug Fixes

* make release workflow and tooling reproducible ([63874da](https://github.com/ichipiro/IE_Event_Bot/commit/63874dabdcdeae05f5e18e82e3598a9f2c0b753a))
* make repository workflow reproducible ([43d5924](https://github.com/ichipiro/IE_Event_Bot/commit/43d5924745508cca238bb0de02e7e9e824e0980d))
* **release:** define manifest package ([786e334](https://github.com/ichipiro/IE_Event_Bot/commit/786e3348b38c2960492e0105b867b33c02a81356))
* **release:** define manifest package ([551b39e](https://github.com/ichipiro/IE_Event_Bot/commit/551b39ee14ee6f87e6465f08e6e856730dda63c8))
* repair merge and release workflow ([1ebd88c](https://github.com/ichipiro/IE_Event_Bot/commit/1ebd88c83f95bdc22f6da99680581b0fd845fa35))

## [Unreleased]
